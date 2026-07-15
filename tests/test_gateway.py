import os

os.environ["DATABASE_URL"] = "sqlite://"      # in-memory
os.environ["GRACE_PERIOD_SECONDS"] = "0"      # bill immediately in tests
for real_mode_key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OLLAMA_MODEL", "GROQ_API_KEY"):
    os.environ.pop(real_mode_key, None)       # tests always use stub mode

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from app.main import app
from app.providers import anthropic_like
from app.storage import engine

TENANT_A = {"X-API-Key": "demo-key-a"}
TENANT_B = {"X-API-Key": "demo-key-b"}
ADMIN = {"X-API-Key": "admin-key"}


@pytest.fixture()
def client():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    anthropic_like._seen_system_prompts.clear()
    with TestClient(app) as c:
        yield c


def chat(client, model, messages, headers=TENANT_A):
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": messages},
        headers=headers,
    )


def test_auth_required(client):
    resp = chat(client, "llama-3.3-70b", [{"role": "user", "content": "hi"}], headers={})
    assert resp.status_code == 401


def test_unknown_model_is_404(client):
    resp = chat(client, "nope", [{"role": "user", "content": "hi"}])
    assert resp.status_code == 404


def test_openai_style_call_is_metered(client):
    resp = chat(client, "llama-3.3-70b", [{"role": "user", "content": "hello world" * 10}])
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"]["input"] > 0 and body["usage"]["output"] > 0
    assert "prompt_tokens" in body["raw_usage"]          # provider schema kept
    assert float(body["cost"]["capacity_units"]) > 0


def test_anthropic_cache_write_then_read(client):
    big_system = {"role": "system", "content": "policy text " * 50}
    user = {"role": "user", "content": "question?"}

    first = chat(client, "claude-opus", [big_system, user]).json()
    assert first["usage"]["cache_write"] > 0 and first["usage"]["cache_read"] == 0

    second = chat(client, "claude-opus", [big_system, user]).json()
    assert second["usage"]["cache_read"] > 0 and second["usage"]["cache_write"] == 0
    # cache read is cheaper than cache write
    assert float(second["cost"]["capacity_units"]) < float(
        first["cost"]["capacity_units"]
    )


def test_vertex_style_image_tokens(client):
    resp = chat(
        client,
        "gemini-flash",
        [{"role": "user", "content": "[image] describe this"}],
    ).json()
    assert resp["usage"]["image"] == 258
    assert resp["cost"]["breakdown"].get("image") is not None


def test_usage_is_tenant_isolated(client):
    chat(client, "llama-3.3-70b", [{"role": "user", "content": "aaaa"}], headers=TENANT_A)
    chat(client, "llama-3.3-70b", [{"role": "user", "content": "bbbb"}], headers=TENANT_B)

    a = client.get("/v1/usage", headers=TENANT_A).json()
    assert a["tenant"] == "tenant-a"
    assert a["by_model"]["llama-3.3-70b"]["calls"] == 1


def test_billing_run_is_idempotent(client):
    chat(client, "llama-3.3-70b", [{"role": "user", "content": "meter me"}])
    chat(client, "claude-opus", [{"role": "user", "content": "me too"}])

    first = client.post("/v1/admin/billing/run", headers=ADMIN).json()
    assert first["processed_records"] == 2 and first["billed_rows"] == 2

    # Re-running immediately must not double-bill anything.
    second = client.post("/v1/admin/billing/run", headers=ADMIN).json()
    assert second["processed_records"] == 0 and second["billed_rows"] == 0


def test_verifier_passes_on_clean_data(client):
    chat(client, "llama-3.3-70b", [{"role": "user", "content": "check my math"}])
    chat(client, "gemini-flash", [{"role": "user", "content": "[image] hi"}])

    report = client.post("/v1/admin/verify", headers=ADMIN).json()
    assert report["checked"] == 2 and report["passed"] is True


def test_admin_endpoints_reject_tenant_keys(client):
    resp = client.post("/v1/admin/billing/run", headers=TENANT_A)
    assert resp.status_code == 401


def test_jwt_token_exchange_and_bearer_auth(client):
    tok = client.post("/v1/auth/token", headers=TENANT_A)
    assert tok.status_code == 200
    body = tok.json()
    assert body["token_type"] == "bearer" and body["expires_in"] > 0

    bearer = {"Authorization": f"Bearer {body['access_token']}"}
    resp = chat(client, "llama-3.3-70b", [{"role": "user", "content": "hi"}], headers=bearer)
    assert resp.status_code == 200

    usage = client.get("/v1/usage", headers=bearer).json()
    assert usage["tenant"] == "tenant-a"  # JWT resolved to the right tenant


def test_token_exchange_requires_valid_key(client):
    resp = client.post("/v1/auth/token", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_expired_jwt_rejected(client):
    import time

    import jwt as pyjwt

    from app.auth import JWT_ALGORITHM, JWT_SECRET

    expired = pyjwt.encode(
        {"sub": "tenant-a", "exp": int(time.time()) - 10},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    resp = client.get("/v1/usage", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_tampered_jwt_rejected(client):
    import time

    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "tenant-a", "exp": int(time.time()) + 600},
        "attacker-secret",  # signed with the WRONG secret
        algorithm="HS256",
    )
    resp = client.get("/v1/usage", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401

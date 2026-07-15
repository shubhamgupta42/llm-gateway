# LLM Gateway with Metering

A production-patterned **LLM gateway** built with FastAPI — one API in front
of many model providers, with a complete **metering pipeline** at its core.
Every call is authenticated, routed, measured, priced in exact Decimal math,
billed idempotently, and independently verified.

```
        ┌──────────────────────── LLM GATEWAY ────────────────────────┐
client ─┤ auth (API key or JWT)                                       │
        │   └─ route by model ──► provider adapter (Groq / Anthropic  │
        │                         / Gemini / Ollama / stubs)          │
        │            normalize usage ──► rate (Decimal) ──► ledger    │
        └──────────────────────────────────────────────────┬──────────┘
                                                            │
             billing job (idempotent, grace period) ◄───────┤
             verifier (independent recomputation)   ◄───────┘
```

**Why this project:** every company running LLMs at scale has this layer —
it's how platforms attribute cost per team, enforce access, and prove their
bills are right. This repo is that architecture, small enough to read in one
sitting, real enough to serve actual models (Groq / Gemini free tiers,
Ollama locally, Claude API).

## Features

- **Two auth schemes on one dependency** — API keys, plus a key→JWT exchange with signature and expiry enforcement
- **Provider integration in three real API styles** — OpenAI-compatible (Groq/Ollama), the official Anthropic SDK, and Gemini's native REST endpoint
- **Usage normalization at the edge** — every provider reports tokens in its own schema; adapters translate to one internal record
- **Billing-grade money math** — exact `Decimal` arithmetic, list prices as config data, unknown token categories fail loudly instead of under-billing
- **Idempotent billing** — watermark + grace-period aggregation; a crashed or repeated run can never double-bill
- **Independent verification** — every stored charge is recomputed from raw token counts and proven
- **Fully testable offline** — deterministic provider stubs; 13 `pytest` tests, no network or keys required

## Quickstart

```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
# interactive docs: http://127.0.0.1:8000/docs
```

Make a metered call (works offline — providers default to deterministic stubs):

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "X-API-Key: demo-key-a" -H "Content-Type: application/json" \
  -d '{"model": "llama-3.3-70b", "messages": [{"role": "user", "content": "hello"}]}'
```

Every response carries three views of the same call:

```json
{
  "content":   "...",                                              // the answer
  "usage":     {"input": 43, "output": 87, "cache_read": 0, ...},  // normalized
  "raw_usage": {"prompt_tokens": 43, "completion_tokens": 87},     // provider's own schema
  "cost":      {"breakdown": {...}, "units": "0.0000254",          // USD (list price)
                "capacity_units": "0.0000508"}                     // platform units
}
```

Then run the whole billing pipeline:

```bash
curl -s http://127.0.0.1:8000/v1/usage -H "X-API-Key: demo-key-a"                      # my ledger
curl -s -X POST http://127.0.0.1:8000/v1/admin/billing/run -H "X-API-Key: admin-key"   # bill it (run twice!)
curl -s -X POST http://127.0.0.1:8000/v1/admin/verify -H "X-API-Key: admin-key"        # prove it
```

Tests: `.venv/bin/pytest -q`

---

## How it works — expand any section

<details>
<summary><b>🚀 Why FastAPI for AI services (and sync vs async)</b></summary>

<br>

Three reasons FastAPI became the default for AI serving:

1. **Pydantic contracts.** Request/response shapes are typed classes; invalid
   input gets a clean 422 before your code runs, and the interactive docs at
   `/docs` are generated from the same models.

   ```python
   class ChatRequest(BaseModel):
       model: str
       messages: list[Message] = Field(min_length=1)
   ```

2. **Dependency injection.** Auth, DB sessions, and shared resources are
   declared as `Depends(...)` — separated from business logic and swappable
   in tests. This repo's JWT support was added **without touching a single
   route**, because routes only depend on `require_tenant`.

3. **Async-native.** A gateway spends its life waiting on provider APIs.
   The rule of thumb used here:
   - `async def` + `await` for I/O-bound work (calling providers) — the
     event loop serves other requests while waiting.
   - plain `def` for quick DB work — FastAPI runs it in a threadpool.
   - **Never** put blocking calls inside `async def` — that freezes the
     whole server. It's the #1 FastAPI bug in ML services.

</details>

<details>
<summary><b>📁 Project structure</b></summary>

<br>

```
llm-gateway/
├── app/
│   ├── main.py              # FastAPI app, lifespan, router wiring
│   ├── config.py            # tenants, model registry, PRICING (data, not code)
│   ├── auth.py              # API-key + JWT verification (one dependency)
│   ├── schemas.py           # Pydantic request/response contracts
│   ├── rating.py            # tokens → money (Decimal)
│   ├── storage.py           # SQLModel tables: ledger, billed rows, watermark
│   ├── providers/
│   │   ├── base.py          # NormalizedUsage, ProviderResult, UpstreamError
│   │   ├── openai_like.py   # Groq (real) / Ollama (real) / stub
│   │   ├── anthropic_like.py# Claude API via official SDK (real) / stub
│   │   └── vertex_like.py   # Gemini native generateContent (real) / stub
│   └── routers/
│       ├── auth.py          # POST /v1/auth/token  (key → JWT exchange)
│       ├── chat.py          # POST /v1/chat/completions  (the gateway)
│       ├── usage.py         # GET  /v1/usage  (tenant-scoped ledger)
│       └── admin.py         # billing job + verifier
└── tests/
    └── test_gateway.py      # 13 tests, all offline
```

The layering rule: **routers** handle HTTP, **providers** handle integration,
**rating/storage** handle money — nothing reaches across a layer.

</details>

<details>
<summary><b>🔐 Authentication: API keys AND JWT bearer tokens</b></summary>

<br>

Every tenant endpoint accepts two schemes through one dependency:

**1. API key** — static shared secret, `X-API-Key: demo-key-a`. Simple,
never expires, requires a lookup per request. Compared with
`secrets.compare_digest` (constant-time — timing attacks matter even here).

**2. JWT bearer token** — exchange the key for a short-lived signed token:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
  -H "X-API-Key: demo-key-a" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s http://127.0.0.1:8000/v1/usage -H "Authorization: Bearer $TOKEN"
```

The token carries its own proof — tenant (`sub`), scopes, expiry (`exp`),
all signed with HS256:

```python
payload = {
    "sub": tenant,                    # who the token represents
    "scope": "chat usage",            # what it may do
    "iat": now,
    "exp": now + TOKEN_TTL_SECONDS,   # hard expiry — the point of JWTs
}
return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
```

Verification is a **signature check, no lookup** — stateless. Expired
tokens and tokens forged with the wrong secret both get a 401 (there are
tests for both). This mirrors the OAuth2 client-credentials pattern used
for service-to-service auth in enterprise platforms.

⚠️ Worth stating explicitly: **JWTs are signed, not encrypted.** Anyone
can base64-decode the payload and read the claims — the signature only
prevents *changing* them. That's why no secrets ever go inside a token.

</details>

<details>
<summary><b>🔀 The gateway endpoint — request lifecycle</b></summary>

<br>

`POST /v1/chat/completions` (in `app/routers/chat.py`) runs the full chain:

```
authenticate ─► route by model ─► call adapter ─► normalize usage
      ─► rate (Decimal) ─► persist UsageRecord ─► respond
```

```python
@router.post("/chat/completions", response_model=ChatResponse)
def chat_completions(body, tenant=Depends(require_tenant), session=Depends(get_session)):
    entry = MODEL_REGISTRY.get(body.model)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown model '{body.model}'")
    try:
        result = adapter(body.model, messages)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))   # provider's fault, not ours
    cost = rate(body.model, result.usage)
    session.add(UsageRecord(tenant=tenant, ...))                # metering IN the request path
```

Design choices worth noticing:

- **Metering is in the request path on purpose** — if the call can't be
  recorded, it isn't served.
- **Status codes are part of the contract**: unknown model → 404, bad
  auth → 401, malformed body → 422 (automatic via Pydantic), provider
  failure → **502** (never an unhandled 500 — an upstream outage must not
  look like a gateway bug).

</details>

<details>
<summary><b>🔌 Provider adapters — the real work of API integration</b></summary>

<br>

Every provider reports usage in its **own** schema. Adapters normalize at
the edge, so rating/reporting never see provider-specific shapes:

| Adapter | Usage schema | Design note |
|---|---|---|
| `openai_like` | `prompt_tokens` / `completion_tokens` | one schema covers a whole family — Groq and Ollama share a single code path because both are OpenAI-compatible; adding another such provider is config, not code |
| `anthropic_like` | **four categories**: input, output, `cache_creation_input_tokens`, `cache_read_input_tokens` | prompt caching has its own prices — the first call *writes* the cache (1.25× input rate), repeats *read* it (0.1×) |
| `vertex_like` | `usageMetadata` with per-**modality** arrays (TEXT vs IMAGE) | the flat total can't be trusted — the adapter walks the detail arrays; image tokens bill at their own rate, and `thoughtsTokenCount` (thinking tokens) is billed as output but is **not** inside `candidatesTokenCount`, so it is added explicitly to avoid under-billing |

The common contract (in `providers/base.py`):

```python
@dataclass
class NormalizedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    image_tokens: int = 0
```

Each adapter also has a **stub mode** (no env vars → deterministic fake,
no network) so the repo runs and tests offline. Real mode is a per-adapter
env switch — see "Running with REAL models" below.

</details>

<details>
<summary><b>💰 Rating — tokens → money, and why Decimal</b></summary>

<br>

The entire billing formula lives in one function (`app/rating.py`):

```
units          = Σ over categories ( tokens / 1000 × factor[category] )
capacity_units = units × CAPACITY_UNIT_MULTIPLIER
```

Worked example from a real call through this gateway (Groq, Llama-3.3-70B):

```
input:  43 tokens × $0.00059/1k = $0.00002537
output: 87 tokens × $0.00079/1k = $0.00006873
units  = $0.00009410      → × 2.0 = 0.0001882 capacity units
```

Deliberate choices:

- **`Decimal`, never `float`** — the verifier compares charges for
  *equality*; float rounding (0.1 + 0.2 ≠ 0.3) produces phantom mismatches.
  Amounts are stored as strings so they stay exact in the database.
- **Prices are data, not code** (`config.py`): each model maps to its
  provider and per-category factors — the providers' **public list prices**,
  cited with URL and as-of date. Onboarding a model = one dict entry.
- **Unknown token category → raise.** If a provider starts reporting a
  category the price list doesn't know, the gateway fails loudly instead
  of silently under-billing. In billing, "fail loud" beats "leak quiet".
- The `CAPACITY_UNIT_MULTIPLIER` shows where a real platform layers its
  own pricing (margin, packaging) on top of list price.

</details>

<details>
<summary><b>🧾 The billing job — idempotency via watermark + grace period</b></summary>

<br>

`POST /v1/admin/billing/run` aggregates the ledger per tenant+model and
"sends it to billing". The pattern that makes it safe:

```python
window_start = state.last_processed_at            # the WATERMARK
window_end   = now - GRACE_PERIOD_SECONDS         # never bill right up to "now"

records = ledger rows in (window_start, window_end]
... aggregate and "send" ...

state.last_processed_at = window_end              # advance ONLY after success
session.commit()
```

- **Crash mid-run?** The watermark never moved → next run replays the same
  window. Nothing lost.
- **Run it twice?** The second window starts exactly where the first ended
  → processes 0 records. Nothing double-billed. (Run the curl twice and
  watch `window_start` of run 2 equal `window_end` of run 1.)
- **Grace period** — usage rows can arrive late; a job that bills up to
  "now" bills partial data. Stop 60s short and let things settle.

This is the same watermark pattern used by production metering pipelines,
Kafka consumer offsets, and CDC replication.

</details>

<details>
<summary><b>✅ The verifier — prove the numbers, don't trust them</b></summary>

<br>

`POST /v1/admin/verify` re-derives **every** stored charge from the raw
token counts and compares:

```python
for record in ledger:
    expected = rate(record.model, usage_from(record))   # recompute from scratch
    if Decimal(expected) != Decimal(record.capacity_units):
        mismatches.append(...)
```

The principle: the verifier deliberately does **not** trust the stored
number — an independent recomputation is the only way a shared math bug,
a post-hoc price change, or a corrupted row can't pass silently.

In a full platform this becomes the "three-number argument": the charge
computed at request time, the charge recomputed by the verifier, and the
charge the billing system actually invoiced must all agree — and *which
pair disagrees* tells you *where* the bug is.

</details>

<details>
<summary><b>🌐 Running with REAL models (two free options)</b></summary>

<br>

By default all providers are stubs (free, offline). Each adapter switches
to a real upstream when its env var is set — mix and match freely:

| Gateway model | Real upstream | Env vars | Cost |
|---|---|---|---|
| `llama-3.3-70b` | **Groq** (OpenAI-compatible cloud) | `GROQ_API_KEY` (+ `GROQ_MODEL`) | **Free** — [console.groq.com](https://console.groq.com) |
| `llama-3.3-70b` | **Ollama** (local models) | `OLLAMA_MODEL` (+ `OLLAMA_URL`) | **Free** — no account |
| `gemini-flash` | **Gemini API** (native `generateContent`) | `GEMINI_API_KEY` (+ `GEMINI_MODEL`) | **Free tier** — [aistudio.google.com](https://aistudio.google.com), no card |
| `claude-opus` | **Claude API** (official SDK) | `ANTHROPIC_API_KEY` (+ `ANTHROPIC_MODEL`) | Paid API credits |

```bash
export GROQ_API_KEY=gsk_...
.venv/bin/uvicorn app.main:app --reload
# "model": "llama-3.3-70b" now returns real answers with real token counts
```

With a real upstream, `usage`/`raw_usage` carry the provider's **real**
numbers and the whole pipeline runs on them. Things to notice:

- **Anthropic**: the system prompt is sent with `cache_control`, so
  repeated large prompts show real `cache_read_input_tokens` (above the
  model's ~4k-token cache minimum).
- **Gemini**: thinking tokens (`thoughtsTokenCount`) are added to output —
  the under-billing trap described in the adapters section.
- **All**: provider failures surface as the gateway's own 502. Kill your
  Ollama server or use a broken key to see it.
- Tests always run in stub mode; secrets live in a gitignored `.env`.

</details>

<details>
<summary><b>🧪 Testing</b></summary>

<br>

13 tests, all offline (`tests/test_gateway.py`), using FastAPI's in-process
`TestClient` and an in-memory SQLite database:

- auth: missing key → 401; admin endpoints reject tenant keys
- JWT: exchange works; **expired** token → 401; **forged** signature → 401
- one test per provider style, including cache write→read and image tokens
- tenant isolation: tenant A never sees tenant B's usage
- **idempotency**: the billing job runs twice; the second run must process 0
- the verifier passes on clean data

Test-infrastructure decisions: env vars are set *before* importing the app
(in-memory DB, zero grace period, stub mode forced), and tables are
dropped/recreated per test for isolation.

</details>

<details>
<summary><b>🛠️ Roadmap — planned extensions</b></summary>

<br>

- **Streaming** (`StreamingResponse` / SSE) — and meter from the *final*
  stream event, since usage arrives at the end of a stream.
- **WebSocket adapter** — realtime audio models report separate audio-token
  categories, and their output total already *includes* audio (a classic
  double-count trap).
- **Rate limiting per tenant** — you already have the usage ledger; enforce
  a budget from it.
- **Point a real app at it** — any OpenAI-style client with a configurable
  `base_url` can route through this gateway and get usage tracking for free
  (add an OpenAI-compatible response shape first).
- **Persistent Postgres + Alembic migrations** instead of SQLite.

</details>

---

## API summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/v1/auth/token` | API key | Exchange key for a 30-min JWT |
| POST | `/v1/chat/completions` | key or JWT | The gateway: route, answer, meter |
| GET | `/v1/usage` | key or JWT | Tenant-scoped usage + cost summary |
| POST | `/v1/admin/billing/run` | admin key | Idempotent billing aggregation |
| POST | `/v1/admin/verify` | admin key | Recompute + prove all charges |
| GET | `/healthz` | none | Liveness probe |

**Config via env vars:** `API_KEY`s (`TENANT_A_KEY`, `TENANT_B_KEY`, `ADMIN_API_KEY`),
`JWT_SECRET`, `TOKEN_TTL_SECONDS`, `DATABASE_URL`, `GRACE_PERIOD_SECONDS`,
plus the per-provider keys listed above. Prices in `app/config.py` are
public list prices as of 2026-07 — verify against the cited pages.

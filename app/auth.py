"""Authentication: two interchangeable schemes on the same dependency.

1. API key   — static shared secret in `X-API-Key` (simple, never expires).
2. JWT       — `Authorization: Bearer <jwt>`, obtained from POST /v1/auth/token
               by exchanging the API key. Signed (HS256), carries the tenant
               in `sub` and an expiry in `exp`; verified WITHOUT any lookup —
               the token proves itself. This mirrors the enterprise pattern
               where a service exchanges credentials at an identity provider
               for a short-lived bearer token.

Because auth is a FastAPI dependency, no route changes when a scheme is
added — that is the payoff of dependency injection.
"""

import os
import secrets
import time

import jwt
from fastapi import Header, HTTPException

from .config import ADMIN_KEY, TENANT_KEYS

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-jwt-secret-change-me")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "1800"))  # 30 min


def tenant_for_key(api_key: str) -> str | None:
    for key, tenant in TENANT_KEYS.items():
        if secrets.compare_digest(api_key, key):
            return tenant
    return None


def create_token(tenant: str) -> str:
    now = int(time.time())
    payload = {
        "sub": tenant,               # who the token represents
        "scope": "chat usage",       # what it may do (checked by consumers)
        "iat": now,                  # issued at
        "exp": now + TOKEN_TTL_SECONDS,  # hard expiry — the point of JWTs
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def require_tenant(
    x_api_key: str = Header(default=""),
    authorization: str = Header(default=""),
) -> str:
    # Scheme 1: Bearer JWT — stateless: signature + expiry check, no lookup.
    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=401,
                detail="Token expired — request a new one at /v1/auth/token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=401,
                detail="Invalid token (bad signature or malformed)",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload["sub"]

    # Scheme 2: static API key.
    tenant = tenant_for_key(x_api_key)
    if tenant is not None:
        return tenant

    raise HTTPException(
        status_code=401,
        detail="Authenticate with X-API-Key or Authorization: Bearer <token>",
    )


def require_admin(x_api_key: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_api_key, ADMIN_KEY):
        raise HTTPException(status_code=401, detail="Admin key required")

"""Token exchange: trade a long-lived API key for a short-lived JWT.

This is the OAuth2 client-credentials idea in miniature — a service
authenticates once with its stable credential and receives an expiring
bearer token to use on every subsequent call.
"""

from fastapi import APIRouter, Header, HTTPException

from ..auth import TOKEN_TTL_SECONDS, create_token, tenant_for_key
from ..schemas import TokenResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def issue_token(x_api_key: str = Header(default="")) -> TokenResponse:
    tenant = tenant_for_key(x_api_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return TokenResponse(
        access_token=create_token(tenant),
        expires_in=TOKEN_TTL_SECONDS,
    )

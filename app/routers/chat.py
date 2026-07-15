"""The gateway entrypoint: one endpoint, many providers.

Flow per call: authenticate tenant -> route by model -> provider adapter
-> normalize usage -> rate -> persist a UsageRecord -> respond.
Metering is in the request path on purpose: if we can't record it,
we don't serve it.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..auth import require_tenant
from ..config import MODEL_REGISTRY
from ..providers import ADAPTERS
from ..providers.base import UpstreamError
from ..rating import rate
from ..schemas import ChatRequest, ChatResponse
from ..storage import UsageRecord, get_session

router = APIRouter(prefix="/v1", tags=["gateway"])


@router.post("/chat/completions", response_model=ChatResponse)
def chat_completions(
    body: ChatRequest,
    tenant: str = Depends(require_tenant),
    session: Session = Depends(get_session),
) -> ChatResponse:
    entry = MODEL_REGISTRY.get(body.model)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown model '{body.model}'")

    adapter = ADAPTERS[entry["provider"]]
    try:
        result = adapter(body.model, [m.model_dump() for m in body.messages])
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    cost = rate(body.model, result.usage)

    record = UsageRecord(
        tenant=tenant,
        model=body.model,
        provider=entry["provider"],
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cache_read_tokens=result.usage.cache_read_tokens,
        cache_write_tokens=result.usage.cache_write_tokens,
        image_tokens=result.usage.image_tokens,
        capacity_units=cost["capacity_units"],
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    return ChatResponse(
        id=record.id,
        model=body.model,
        content=result.text,
        usage=result.usage.category_counts(),
        raw_usage=result.raw_usage,
        cost=cost,
    )

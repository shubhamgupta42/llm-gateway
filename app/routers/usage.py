"""Tenant-facing usage reporting: each tenant sees only its own usage,
aggregated per model — the gateway equivalent of a billing cockpit."""

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..auth import require_tenant
from ..schemas import UsageSummary
from ..storage import UsageRecord, get_session

router = APIRouter(prefix="/v1", tags=["usage"])


@router.get("/usage", response_model=UsageSummary)
def usage_summary(
    tenant: str = Depends(require_tenant),
    session: Session = Depends(get_session),
) -> UsageSummary:
    records = session.exec(
        select(UsageRecord).where(UsageRecord.tenant == tenant)
    ).all()

    by_model: dict[str, dict] = {}
    total = Decimal("0")
    for r in records:
        agg = by_model.setdefault(
            r.model,
            {"calls": 0, "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_write_tokens": 0,
             "image_tokens": 0, "capacity_units": Decimal("0")},
        )
        agg["calls"] += 1
        agg["input_tokens"] += r.input_tokens
        agg["output_tokens"] += r.output_tokens
        agg["cache_read_tokens"] += r.cache_read_tokens
        agg["cache_write_tokens"] += r.cache_write_tokens
        agg["image_tokens"] += r.image_tokens
        agg["capacity_units"] += Decimal(r.capacity_units)
        total += Decimal(r.capacity_units)

    for agg in by_model.values():
        agg["capacity_units"] = str(agg["capacity_units"])

    return UsageSummary(
        tenant=tenant, by_model=by_model, total_capacity_units=str(total)
    )

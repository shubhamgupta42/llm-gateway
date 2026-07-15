"""Admin plane: the billing job and the verifier.

POST /v1/admin/billing/run — idempotent window aggregation ("send to the
billing service"). POST /v1/admin/verify — independent recomputation of
every stored charge. In production these are CronJobs, not endpoints;
exposing them as endpoints makes the pattern easy to demo and test.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..auth import require_admin
from ..config import GRACE_PERIOD_SECONDS
from ..providers.base import NormalizedUsage
from ..rating import rate
from ..schemas import BillingRunResult, VerifyReport
from ..storage import BilledUsage, BillingState, UsageRecord, get_session

router = APIRouter(
    prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@router.post("/billing/run", response_model=BillingRunResult)
def run_billing(session: Session = Depends(get_session)) -> BillingRunResult:
    state = session.get(BillingState, 1)
    window_start = state.last_processed_at if state else EPOCH
    if window_start.tzinfo is None:  # SQLite drops tzinfo
        window_start = window_start.replace(tzinfo=timezone.utc)

    # Grace period: never bill right up to "now" — let late rows settle.
    window_end = datetime.now(timezone.utc) - timedelta(seconds=GRACE_PERIOD_SECONDS)

    records = session.exec(
        select(UsageRecord)
        .where(UsageRecord.created_at > window_start)
        .where(UsageRecord.created_at <= window_end)
    ).all()

    groups: dict[tuple[str, str], list[UsageRecord]] = {}
    for r in records:
        groups.setdefault((r.tenant, r.model), []).append(r)

    for (tenant, model), recs in groups.items():
        total = sum(Decimal(r.capacity_units) for r in recs)
        session.add(
            BilledUsage(
                tenant=tenant,
                model=model,
                window_start=window_start,
                window_end=window_end,
                record_count=len(recs),
                capacity_units=str(total),
            )
        )

    # Watermark advances ONLY here, after the "send" succeeded — this is
    # what makes a crashed run safe to replay.
    if state is None:
        state = BillingState(id=1, last_processed_at=window_end)
    else:
        state.last_processed_at = window_end
    session.add(state)
    session.commit()

    return BillingRunResult(
        processed_records=len(records),
        billed_rows=len(groups),
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
    )


@router.post("/verify", response_model=VerifyReport)
def verify(session: Session = Depends(get_session)) -> VerifyReport:
    """Recompute every charge from raw token counts and compare with what
    was stored at request time. The verifier does NOT trust the stored
    number — an independent recomputation is the only way a shared math
    bug can't silently pass."""
    records = session.exec(select(UsageRecord)).all()
    mismatches = []
    for r in records:
        usage = NormalizedUsage(
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_write_tokens=r.cache_write_tokens,
            image_tokens=r.image_tokens,
        )
        expected = rate(r.model, usage)["capacity_units"]
        if Decimal(expected) != Decimal(r.capacity_units):
            mismatches.append(
                {"record_id": r.id, "stored": r.capacity_units, "expected": expected}
            )
    return VerifyReport(
        checked=len(records), mismatches=mismatches, passed=not mismatches
    )

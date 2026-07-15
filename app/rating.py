"""Rating: token counts -> billable capacity units.

Uses Decimal, never float: billing numbers get compared for EQUALITY
downstream (see verify.py), and float rounding produces tiny mismatches
that look like billing bugs.

    units = sum over categories( tokens/1000 * factor[category] )
    capacity_units = units * CAPACITY_UNIT_MULTIPLIER
"""

from decimal import ROUND_HALF_UP, Decimal

from .config import CAPACITY_UNIT_MULTIPLIER, MODEL_REGISTRY
from .providers.base import NormalizedUsage

PRECISION = Decimal("0.0000000001")  # 10 decimal places
THOUSAND = Decimal("1000")


def rate(model: str, usage: NormalizedUsage) -> dict:
    factors: dict[str, Decimal] = MODEL_REGISTRY[model]["factors"]
    breakdown: dict[str, str] = {}
    units = Decimal("0")

    for category, tokens in usage.category_counts().items():
        if tokens == 0:
            continue
        factor = factors.get(category)
        if factor is None:
            # A token category the price list doesn't know = a billing gap.
            # Fail loudly instead of silently under-billing.
            raise ValueError(f"model {model} has no factor for '{category}' tokens")
        cost = (Decimal(tokens) / THOUSAND * factor).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )
        breakdown[category] = str(cost)
        units += cost

    capacity_units = (units * CAPACITY_UNIT_MULTIPLIER).quantize(
        PRECISION, rounding=ROUND_HALF_UP
    )
    return {
        "breakdown": breakdown,
        "units": str(units),
        "capacity_units": str(capacity_units),  # str: Decimal survives JSON/DB
    }

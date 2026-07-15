"""Central configuration: tenants, model registry, pricing.

Everything billing-related is DATA, not code — adding a model or changing
a price is a registry edit. (In production this would live in Helm values /
a config service and be injected as env vars.)

All factors below are made-up example numbers.
"""

import os
from decimal import Decimal

# --- Tenancy: API key -> tenant id (in production: an IdP / OAuth introspection)
TENANT_KEYS: dict[str, str] = {
    os.environ.get("TENANT_A_KEY", "demo-key-a"): "tenant-a",
    os.environ.get("TENANT_B_KEY", "demo-key-b"): "tenant-b",
}
ADMIN_KEY: str = os.environ.get("ADMIN_API_KEY", "admin-key")

# --- Pricing: factors are USD per 1,000 tokens, per token category.
# Values are the providers' PUBLIC list prices (their pages quote per 1M
# tokens; divided by 1000 here). As of 2026-07 — verify against the cited
# pricing pages before relying on them; list prices change.
MODEL_REGISTRY: dict[str, dict] = {
    # Llama 3.3 70B on Groq — https://groq.com/pricing
    # ($0.59 / $0.79 per 1M tokens)
    "llama-3.3-70b": {
        "provider": "openai_like",
        "factors": {
            "input": Decimal("0.00059"),
            "output": Decimal("0.00079"),
        },
    },
    # Claude Opus 4.8 — https://claude.com/pricing (API section)
    # ($5 in / $25 out per 1M; cache read 0.1x input, cache write 1.25x input)
    "claude-opus": {
        "provider": "anthropic_like",
        "factors": {
            "input": Decimal("0.005"),
            "output": Decimal("0.025"),
            "cache_read": Decimal("0.0005"),
            "cache_write": Decimal("0.00625"),
        },
    },
    # Gemini 2.5 Flash — https://ai.google.dev/gemini-api/docs/pricing
    # ($0.30 per 1M input incl. image tokens / $2.50 per 1M output)
    "gemini-flash": {
        "provider": "vertex_like",
        "factors": {
            "input": Decimal("0.0003"),
            "output": Decimal("0.0025"),
            "image": Decimal("0.0003"),
        },
    },
}

# Plan-level conversion from USD list price to the platform's billing unit
# (example value — this is where a real platform layers margin/packaging).
CAPACITY_UNIT_MULTIPLIER = Decimal("2.0")

# Billing job never reads right up to "now": late usage rows must settle first.
GRACE_PERIOD_SECONDS = int(os.environ.get("GRACE_PERIOD_SECONDS", "60"))

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./gateway.db")

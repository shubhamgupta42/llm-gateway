"""Provider adapter contract.

Every provider returns usage in its OWN schema (`raw_usage`). The adapter's
job is to normalize that into the gateway's common token categories so the
rating layer never needs provider-specific logic.
"""

from dataclasses import dataclass, field


class UpstreamError(RuntimeError):
    """A real provider call failed (rate limit, outage, refusal).

    The gateway surfaces this as its own 502 — provider failures must
    never look like gateway bugs."""


@dataclass
class NormalizedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    image_tokens: int = 0

    def category_counts(self) -> dict[str, int]:
        return {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "cache_read": self.cache_read_tokens,
            "cache_write": self.cache_write_tokens,
            "image": self.image_tokens,
        }


@dataclass
class ProviderResult:
    text: str
    raw_usage: dict = field(default_factory=dict)
    usage: NormalizedUsage = field(default_factory=NormalizedUsage)


def estimate_tokens(text: str) -> int:
    """Crude ~4-chars-per-token estimate (stand-in for real tokenizers)."""
    return max(1, len(text) // 4)

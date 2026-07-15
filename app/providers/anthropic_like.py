"""Provider adapter with an Anthropic-style usage schema — FOUR token
categories: input, output, cache_creation (write), cache_read.

Two modes, same interface:

- REAL mode (when ANTHROPIC_API_KEY is set): calls the actual Claude API
  through the official SDK. The system prompt is marked cacheable, and
  the four usage categories come from the provider's real response.
- STUB mode (no key): deterministic simulation — no network, no cost.
  The first request with a given system prompt "writes" the cache,
  identical system prompts afterwards "read" it.

Everything downstream (rating, storage, billing, verification) is
identical in both modes — that is the point of the adapter layer.
"""

import hashlib
import os

from .base import NormalizedUsage, ProviderResult, UpstreamError, estimate_tokens

# --- REAL mode -------------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def _complete_real(model: str, messages: list[dict]) -> ProviderResult:
    import anthropic

    system_text = "\n".join(
        m.get("content", "") for m in messages if m.get("role") == "system"
    )
    chat_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") != "system"
    ]

    kwargs: dict = {}
    if system_text:
        # cache_control demonstrates real prompt-cache billing; note the
        # prefix must exceed the model's minimum (~4k tokens on Opus) to
        # actually cache — below that the cache fields stay 0.
        kwargs["system"] = [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    try:
        resp = _get_client().messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
            max_tokens=1024,
            messages=chat_messages,
            **kwargs,
        )
    except anthropic.RateLimitError as exc:
        raise UpstreamError(f"provider rate limited: {exc.message}")
    except anthropic.APIStatusError as exc:
        raise UpstreamError(f"provider error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        raise UpstreamError("could not reach provider (network error)")

    if resp.stop_reason == "refusal":
        raise UpstreamError("provider declined the request (refusal)")

    text = "".join(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    usage = NormalizedUsage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_input_tokens or 0,
        cache_write_tokens=u.cache_creation_input_tokens or 0,
    )
    return ProviderResult(
        text=text,
        raw_usage=u.model_dump(mode="json"),
        usage=usage,
    )


# --- STUB mode ---------------------------------------------------------------

CACHE_MIN_TOKENS = 32  # stand-in for the real ~1024-4096 token minimum
_seen_system_prompts: set[str] = set()


def _complete_stub(model: str, messages: list[dict]) -> ProviderResult:
    system_text = " ".join(
        m.get("content", "") for m in messages if m.get("role") == "system"
    )
    user_tokens = sum(
        estimate_tokens(m.get("content", ""))
        for m in messages
        if m.get("role") != "system"
    )
    system_tokens = estimate_tokens(system_text) if system_text else 0

    cache_read = cache_write = 0
    input_tokens = user_tokens
    if system_tokens >= CACHE_MIN_TOKENS:
        digest = hashlib.sha256(system_text.encode()).hexdigest()
        if digest in _seen_system_prompts:
            cache_read = system_tokens
        else:
            cache_write = system_tokens
            _seen_system_prompts.add(digest)
    else:
        input_tokens += system_tokens  # too small to cache: billed as input

    answer = f"[{model} stub] considered reply"
    output_tokens = estimate_tokens(answer)

    raw = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
    }
    usage = NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )
    return ProviderResult(text=answer, raw_usage=raw, usage=usage)


def complete(model: str, messages: list[dict]) -> ProviderResult:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _complete_real(model, messages)
    return _complete_stub(model, messages)

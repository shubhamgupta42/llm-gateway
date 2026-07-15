"""Provider adapter with an OpenAI-style usage schema:
`{"prompt_tokens": .., "completion_tokens": .., "total_tokens": ..}`

Three modes, same interface — picked by env vars, first match wins:

- GROQ (GROQ_API_KEY set): calls Groq's free-tier cloud API. Groq speaks
  the OpenAI-compatible protocol, so it shares the code path with Ollama.
- OLLAMA (OLLAMA_MODEL set): calls a local Ollama server — free, offline.
- STUB (neither): deterministic echo, no network.

The point to notice: because both real upstreams are OpenAI-compatible,
one request/usage shape covers a whole family of providers — that is why
"OpenAI-compatible" became the de-facto industry wire format.
"""

import os

import httpx

from .base import NormalizedUsage, ProviderResult, UpstreamError, estimate_tokens


def _call_openai_compatible(
    name: str,
    base_url: str,
    upstream_model: str,
    messages: list[dict],
    headers: dict | None = None,
    timeout: float = 60.0,
) -> ProviderResult:
    try:
        resp = httpx.post(
            f"{base_url}/chat/completions",
            json={"model": upstream_model, "messages": messages},
            headers=headers or {},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise UpstreamError(
            f"{name} error {exc.response.status_code}: {exc.response.text[:200]}"
        )
    except httpx.TransportError:
        raise UpstreamError(f"could not reach {name} (network error / not running)")

    data = resp.json()
    raw = data.get("usage", {})
    usage = NormalizedUsage(
        input_tokens=raw.get("prompt_tokens", 0),
        output_tokens=raw.get("completion_tokens", 0),
    )
    return ProviderResult(
        text=data["choices"][0]["message"]["content"],
        raw_usage=raw,
        usage=usage,
    )


def _complete_stub(model: str, messages: list[dict]) -> ProviderResult:
    prompt_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
    answer = f"[{model} stub] echoing {len(messages)} message(s)"
    completion_tokens = estimate_tokens(answer)

    raw = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    usage = NormalizedUsage(
        input_tokens=raw["prompt_tokens"],
        output_tokens=raw["completion_tokens"],
    )
    return ProviderResult(text=answer, raw_usage=raw, usage=usage)


def complete(model: str, messages: list[dict]) -> ProviderResult:
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        return _call_openai_compatible(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            upstream_model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            messages=messages,
            headers={"Authorization": f"Bearer {groq_key}"},
        )
    if os.environ.get("OLLAMA_MODEL"):
        return _call_openai_compatible(
            name="ollama",
            base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/v1",
            upstream_model=os.environ["OLLAMA_MODEL"],
            messages=messages,
            timeout=120.0,  # local CPU generation can be slow
        )
    return _complete_stub(model, messages)

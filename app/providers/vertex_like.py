"""Provider adapter with a Vertex/Gemini-style usage schema: `usageMetadata`
holding per-MODALITY detail arrays (TEXT vs IMAGE) that must be walked to
split token counts — image tokens are billed at their own rate.

Two modes, same interface:

- REAL mode (when GEMINI_API_KEY is set): calls the Gemini API's native
  `generateContent` endpoint — Google's request schema, not an OpenAI
  wrapper. A free-tier key from https://aistudio.google.com works
  (no payment details required).
- STUB mode (no key): deterministic simulation. A message counts as
  containing an image if its content includes an `[image]` marker.
"""

import os

import httpx

from .base import NormalizedUsage, ProviderResult, UpstreamError, estimate_tokens

TOKENS_PER_IMAGE = 258  # typical fixed per-image token cost


def _complete_real(model: str, messages: list[dict]) -> ProviderResult:
    api_key = os.environ["GEMINI_API_KEY"]
    upstream_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    system_text = "\n".join(
        m.get("content", "") for m in messages if m.get("role") == "system"
    )
    contents = [
        {
            "role": "model" if m.get("role") == "assistant" else "user",
            "parts": [{"text": m.get("content", "")}],
        }
        for m in messages
        if m.get("role") != "system"
    ]
    body: dict = {"contents": contents}
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    try:
        resp = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{upstream_model}:generateContent",
            headers={"x-goog-api-key": api_key},
            json=body,
            timeout=60.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise UpstreamError(
            f"gemini error {exc.response.status_code}: {exc.response.text[:200]}"
        )
    except httpx.TransportError:
        raise UpstreamError("could not reach the Gemini API (network error)")

    data = resp.json()
    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts", []) if candidates else []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        raise UpstreamError("gemini returned no text (safety block or empty reply)")

    meta = data.get("usageMetadata", {})
    # Walk the per-modality arrays — never trust the flat total.
    text_tokens = image_tokens = 0
    for detail in meta.get("promptTokensDetails", []):
        if detail.get("modality") == "IMAGE":
            image_tokens += detail.get("tokenCount", 0)
        else:
            text_tokens += detail.get("tokenCount", 0)
    if not meta.get("promptTokensDetails"):
        text_tokens = meta.get("promptTokenCount", 0)

    # Billing trap: thinking tokens (thoughtsTokenCount) are billed as
    # output but are NOT inside candidatesTokenCount — miss them and you
    # under-bill every call.
    output_tokens = meta.get("candidatesTokenCount", 0) + meta.get(
        "thoughtsTokenCount", 0
    )

    usage = NormalizedUsage(
        input_tokens=text_tokens,
        output_tokens=output_tokens,
        image_tokens=image_tokens,
    )
    return ProviderResult(text=text, raw_usage=meta, usage=usage)


def _complete_stub(model: str, messages: list[dict]) -> ProviderResult:
    text_tokens, image_count = 0, 0
    for m in messages:
        content = m.get("content", "")
        if "[image]" in content or content.startswith("data:image"):
            image_count += 1
        text_tokens += estimate_tokens(content.replace("[image]", ""))

    image_tokens = image_count * TOKENS_PER_IMAGE
    answer = f"[{model} stub] described {image_count} image(s)"
    output_tokens = estimate_tokens(answer)

    prompt_details = [{"modality": "TEXT", "tokenCount": text_tokens}]
    if image_tokens:
        prompt_details.append({"modality": "IMAGE", "tokenCount": image_tokens})

    raw = {
        "usageMetadata": {
            "promptTokenCount": text_tokens + image_tokens,
            "candidatesTokenCount": output_tokens,
            "promptTokensDetails": prompt_details,
        }
    }
    usage = NormalizedUsage(
        input_tokens=text_tokens,
        output_tokens=output_tokens,
        image_tokens=image_tokens,
    )
    return ProviderResult(text=answer, raw_usage=raw, usage=usage)


def complete(model: str, messages: list[dict]) -> ProviderResult:
    if os.environ.get("GEMINI_API_KEY"):
        return _complete_real(model, messages)
    return _complete_stub(model, messages)

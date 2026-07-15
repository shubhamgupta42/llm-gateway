from . import anthropic_like, openai_like, vertex_like

ADAPTERS = {
    "openai_like": openai_like.complete,
    "anthropic_like": anthropic_like.complete,
    "vertex_like": vertex_like.complete,
}

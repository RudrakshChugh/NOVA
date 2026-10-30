from functools import lru_cache

from ..config import settings
from .base import GenerationRequest, GenerationResult, LLMAdapter

__all__ = ["GenerationRequest", "GenerationResult", "LLMAdapter", "adapter_for", "get_adapter"]


def _build(name: str) -> LLMAdapter:
    if name == "ollama":
        from .ollama_adapter import OllamaAdapter

        return OllamaAdapter()
    if name == "anthropic":
        from .anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter()
    if name == "groq":
        from .groq_adapter import GroqAdapter

        return GroqAdapter()
    raise ValueError(f"unknown LLM backend: {name}")


def get_adapter() -> LLMAdapter:
    """The adapter a new run should use. In failover mode this probes the self-hosted machine, so
    it is deliberately *not* cached — whether that machine is switched on changes between runs."""
    if settings.llm_backend == "failover":
        from .failover import FailoverAdapter

        return FailoverAdapter().pick()
    return _cached(settings.llm_backend)


@lru_cache(maxsize=4)
def _cached(name: str) -> LLMAdapter:
    return _build(name)


def adapter_for(backend_id: str | None) -> LLMAdapter | None:
    """The adapter a run already committed to, resolved from the `backend_id` stored on it, or
    None when this process has no such backend.

    Every draft of a run goes through this, so a run started on the self-hosted model finishes on
    it even if the machine is switched off midway — the run fails rather than silently continuing
    on a different model, which would make its candidates incomparable with its own first drafts.
    """
    if not backend_id:
        return None
    try:
        return _cached(backend_id.split(":", 1)[0])
    except ValueError:
        return None

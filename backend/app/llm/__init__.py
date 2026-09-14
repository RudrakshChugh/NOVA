from functools import lru_cache

from ..config import settings
from .base import GenerationRequest, GenerationResult, LLMAdapter

__all__ = ["GenerationRequest", "GenerationResult", "LLMAdapter", "get_adapter"]


@lru_cache(maxsize=1)
def get_adapter() -> LLMAdapter:
    if settings.llm_backend == "ollama":
        from .ollama_adapter import OllamaAdapter

        return OllamaAdapter()
    if settings.llm_backend == "anthropic":
        from .anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter()
    raise ValueError(f"unknown LLM backend: {settings.llm_backend}")

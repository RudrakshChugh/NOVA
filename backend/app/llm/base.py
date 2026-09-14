"""LLM backend adapter interface (plan §4). The orchestrator only depends on this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenerationRequest:
    system: str
    user: str
    json_schema: dict
    temperature: float | None
    seed: int | None


@dataclass
class GenerationResult:
    data: dict  # parsed JSON matching json_schema
    model_version: str
    # What the backend actually honoured. None means the backend does not accept the parameter,
    # which is logged as NULL on generation_runs rather than a value it never used.
    temperature: float | None
    seed: int | None


class LLMAdapter(ABC):
    backend_id: str

    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...

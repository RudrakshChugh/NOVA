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


def access_headers() -> dict[str, str]:
    """Cloudflare Access service-token headers, sent on every call to a self-hosted backend that is
    reached over a tunnel. Without them the tunnel would be an unauthenticated public endpoint."""
    from ..config import settings

    if settings.cf_access_client_id and settings.cf_access_client_secret:
        return {
            "CF-Access-Client-Id": settings.cf_access_client_id,
            "CF-Access-Client-Secret": settings.cf_access_client_secret,
        }
    return {}


class LLMAdapter(ABC):
    backend_id: str

    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...

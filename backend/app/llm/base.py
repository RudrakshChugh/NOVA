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


class MalformedOutput(RuntimeError):
    """The backend answered, but not with something a question can be read out of: text that is
    not JSON, JSON of the wrong shape, or a reply cut off at the token limit.

    Distinct from a transport failure because the remedy is different. An unreachable backend
    will be unreachable for the next row too, so the run stops; a malformed reply is one bad
    sample, and asking again usually gets a good one."""


def parse_json_object(text: str | None, backend: str) -> dict:
    """The model's reply as a JSON object, or MalformedOutput saying what came back instead.

    Models wrap JSON in a Markdown fence often enough that rejecting it would waste a retry on
    something trivially recoverable, so one fence is stripped before parsing."""
    import json

    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw[:4].lower() == "json":
            raw = raw[4:].strip()
    if not raw:
        raise MalformedOutput(f"{backend} returned an empty reply")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise MalformedOutput(f"{backend} returned text that is not JSON ({e}): {raw[:120]!r}") from e
    if not isinstance(data, dict):
        raise MalformedOutput(f"{backend} returned JSON that is not an object: {raw[:120]!r}")
    return data


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

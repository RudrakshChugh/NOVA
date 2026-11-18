"""Self-hosted open-weight adapter (local Ollama server, JSON-schema constrained output)."""

import httpx

from ..config import settings
from .base import (
    GenerationRequest, GenerationResult, LLMAdapter, MalformedOutput, access_headers, parse_json_object,
)


class OllamaAdapter(LLMAdapter):
    def __init__(self) -> None:
        self.model = settings.ollama_model
        self.backend_id = f"ollama:{self.model}"
        self.url = settings.ollama_url.rstrip("/")

    def generate(self, req: GenerationRequest) -> GenerationResult:
        options = {"temperature": req.temperature}
        if req.seed is not None:
            options["seed"] = req.seed
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}],
            "format": req.json_schema,
            "options": options,
            "think": False,  # reasoning models (qwen3) must emit only the JSON
            "stream": False,
        }
        try:
            r = httpx.post(
                f"{self.url}/api/chat", json=payload,
                headers=access_headers(), timeout=settings.ollama_timeout,
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama error {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"Ollama unreachable at {self.url}: {e}") from e
        try:
            body = r.json()
            content = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise MalformedOutput(f"Ollama reply had no message content: {r.text[:120]!r}") from e
        if body.get("done_reason") == "length":
            raise MalformedOutput("Ollama stopped at the token limit before the JSON was complete")
        return GenerationResult(
            data=parse_json_object(content, "Ollama"),
            model_version=body.get("model", self.model),
            temperature=req.temperature,
            seed=req.seed,
        )

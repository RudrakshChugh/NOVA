"""Self-hosted open-weight adapter (local Ollama server, JSON-schema constrained output)."""

import json

import httpx

from ..config import settings
from .base import GenerationRequest, GenerationResult, LLMAdapter, access_headers


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
        body = r.json()
        return GenerationResult(
            data=json.loads(body["message"]["content"]),
            model_version=body["model"],
            temperature=req.temperature,
            seed=req.seed,
        )

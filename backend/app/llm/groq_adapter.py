"""Hosted fallback over Groq's OpenAI-compatible endpoint.

Used when the self-hosted machine is unreachable. Groq accepts a JSON-object constraint but not a
full JSON *schema*, so the schema is restated in the system prompt and the parsed object is checked
for the keys the orchestrator relies on — a missing key has to fail here, not three layers later.
"""

import json

import httpx

from ..config import settings
from .base import GenerationRequest, GenerationResult, LLMAdapter


class GroqAdapter(LLMAdapter):
    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        self.model = settings.groq_model
        self.backend_id = f"groq:{self.model}"
        self.url = settings.groq_url.rstrip("/")

    def generate(self, req: GenerationRequest) -> GenerationResult:
        required = req.json_schema.get("required") or list((req.json_schema.get("properties") or {}))
        system = (
            f"{req.system}\n\n"
            "Reply with a single JSON object and nothing else. It must match this schema exactly:\n"
            f"{json.dumps(req.json_schema)}"
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": req.user}],
            "response_format": {"type": "json_object"},
            "temperature": req.temperature,
            "stream": False,
        }
        if req.seed is not None:
            payload["seed"] = req.seed
        try:
            r = httpx.post(
                f"{self.url}/chat/completions", json=payload,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                timeout=settings.groq_timeout,
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Groq error {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"Groq unreachable at {self.url}: {e}") from e

        body = r.json()
        data = json.loads(body["choices"][0]["message"]["content"])
        missing = [k for k in required if k not in data]
        if missing:
            raise RuntimeError(f"Groq returned JSON missing {', '.join(missing)}")
        return GenerationResult(
            data=data,
            model_version=body.get("model", self.model),
            temperature=req.temperature,
            # Groq accepts `seed` on a best-effort basis and does not report back whether it was
            # applied, so it is recorded as unhonoured rather than claimed.
            seed=None,
        )

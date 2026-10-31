"""Hosted fallback over Groq's OpenAI-compatible endpoint.

Used when the self-hosted machine is unreachable. Groq accepts a JSON-object constraint but not a
full JSON *schema*, so the schema is restated in the system prompt and the parsed object is checked
for the keys the orchestrator relies on — a missing key has to fail here, not three layers later.
"""

import json
import time

import httpx

from ..config import settings
from .base import GenerationRequest, GenerationResult, LLMAdapter

# A free tier limits requests per minute, and one question can take four calls, so a run of any
# size will meet a 429. Losing a whole run to one is not acceptable when waiting a few seconds
# would have carried it through. 5xx are included because they are transient by definition.
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


def _retry_after(response: httpx.Response) -> float | None:
    """Groq says how long to wait. Honour it rather than guessing — guessing either wastes time or
    retries too early and burns another request against the same limit."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # the HTTP-date form; fall back to the backoff schedule


class GroqAdapter(LLMAdapter):
    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        self.model = settings.groq_model
        self.backend_id = f"groq:{self.model}"
        self.url = settings.groq_url.rstrip("/")

    def _post(self, payload: dict) -> httpx.Response:
        """One call, retried through rate limits and transient server errors."""
        backoff = 1.0
        for attempt in range(settings.groq_max_retries + 1):
            try:
                r = httpx.post(
                    f"{self.url}/chat/completions", json=payload,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    timeout=settings.groq_timeout,
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"Groq unreachable at {self.url}: {e}") from e

            if r.status_code not in RETRYABLE:
                if r.is_error:
                    raise RuntimeError(f"Groq error {r.status_code}: {r.text[:200]}")
                return r

            if attempt == settings.groq_max_retries:
                raise RuntimeError(
                    f"Groq error {r.status_code} after {attempt + 1} attempts: {r.text[:200]}"
                )
            time.sleep(min(_retry_after(r) or backoff, settings.groq_max_backoff))
            backoff *= 2
        raise AssertionError("unreachable")  # the loop either returns or raises

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
        r = self._post(payload)
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

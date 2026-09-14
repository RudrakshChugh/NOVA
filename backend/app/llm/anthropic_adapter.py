"""Hosted API adapter (Anthropic Messages API, structured JSON output)."""

import json

import anthropic

from ..config import settings
from .base import GenerationRequest, GenerationResult, LLMAdapter


class AnthropicAdapter(LLMAdapter):
    def __init__(self) -> None:
        self.model = settings.anthropic_model
        self.backend_id = f"anthropic:{self.model}"
        self.client = anthropic.Anthropic()

    def generate(self, req: GenerationRequest) -> GenerationResult:
        # Claude 4.6+ models reject sampling parameters (temperature/top_p) with a 400 and the API
        # has no seed parameter, so neither is forwarded; both are reported as None (logged NULL).
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=req.system,
                messages=[{"role": "user", "content": req.user}],
                output_config={"format": {"type": "json_schema", "schema": req.json_schema}},
            )
        except anthropic.RateLimitError as e:
            raise RuntimeError(f"LLM rate limited: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"LLM API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise RuntimeError("LLM connection error") from e

        if response.stop_reason == "refusal":
            raise RuntimeError("LLM refused the request")
        text = next(b.text for b in response.content if b.type == "text")
        return GenerationResult(
            data=json.loads(text), model_version=response.model, temperature=None, seed=None
        )

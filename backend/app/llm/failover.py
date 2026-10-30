"""Self-hosted first, hosted fallback.

The self-hosted machine is reached over a Cloudflare tunnel and is only up when it is switched on,
so the backend is chosen by probing rather than by configuration. Two rules keep this from
corrupting the record:

  * the choice is made once, when a run starts, and the chosen `backend_id` is stored on the run.
    Every draft of that run then resolves back to the same adapter, so a run can never be half
    written by one model and half by another;
  * an evaluation study should pin a backend explicitly instead of using this. Comparing arms
    across two different models would measure the model change, not the auditing layers (plan
    §3.3.2 fixes the backend for exactly this reason).
"""

import httpx

from ..config import settings
from .base import GenerationRequest, GenerationResult, LLMAdapter, access_headers


def self_hosted_reachable() -> bool:
    url = (settings.ollama_url or "").rstrip("/")
    if not url:
        return False
    try:
        r = httpx.get(f"{url}/api/tags", headers=access_headers(), timeout=settings.failover_probe_timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


class FailoverAdapter(LLMAdapter):
    """Resolves to a concrete adapter. Never used to generate directly — `pick()` is called when a
    run starts and the concrete adapter it returns is what does the work."""

    backend_id = "failover"

    def pick(self) -> LLMAdapter:
        if self_hosted_reachable():
            from .ollama_adapter import OllamaAdapter

            return OllamaAdapter()
        from .groq_adapter import GroqAdapter

        return GroqAdapter()

    def generate(self, req: GenerationRequest) -> GenerationResult:
        return self.pick().generate(req)

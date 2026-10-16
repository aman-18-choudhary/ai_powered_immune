"""OPT-IN live test of the Live Protection explainer against a LOCAL Ollama
(``OLLAMA_LIVE_TESTS=1``; ``OLLAMA_URL`` default http://127.0.0.1:11434, ``OLLAMA_MODEL`` default
qwen2.5:0.5b). The facts are what the detector produces for a digital-arrest call. Prints the raw
model answer and the grounding decision; asserts only what must hold whatever the model writes:
the result is accepted grounded text or a named rejection, never unchecked text. Run with ``-s``.
"""

import os

import httpx
import pytest

from live_audio.explain import OllamaExplainer, build_prompt

pytestmark = pytest.mark.skipif(
    os.getenv("OLLAMA_LIVE_TESTS") != "1", reason="set OLLAMA_LIVE_TESTS=1 to use a live Ollama"
)

FACTS = {
    "level": "CRITICAL",
    "score": 94,
    "indicators": [
        {"code": "DIGITAL_ARREST_PHRASE", "label": "Says you are under 'digital arrest'",
         "excerpts": ["You are under digital arrest."]},
        {"code": "AUTHORITY_IMPERSONATION", "label": "Claims to be police, CBI or another agency",
         "excerpts": ["This is officer Sharma calling from the CBI."]},
        {"code": "ISOLATION_DEMAND", "label": "Tells you not to tell anyone or to stay on the call",
         "excerpts": ["Do not tell anyone about this call."]},
    ],
}  # fmt: skip


async def test_live_explainer_is_grounded_or_rejected():
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
    timeout = float(os.getenv("OLLAMA_TIMEOUT_S", "30"))
    seen: list[str] = []

    class Recording(httpx.AsyncHTTPTransport):
        """Keeps the raw answer of the SAME call that the grounding checks judge."""

        async def handle_async_request(self, request):
            resp = await super().handle_async_request(request)
            if request.url.path == "/api/generate":
                await resp.aread()
                seen.append(httpx.Response(200, content=resp.content).json().get("response"))
            return resp

    ex = OllamaExplainer(url, model, timeout, transport=Recording())
    if await ex.probe() != "reachable":
        pytest.skip("ollama not reachable")
    print(f"\n[live] prompt={build_prompt(FACTS)!r}")
    res = await ex.explain(FACTS)
    print(f"[live] raw model answer judged={seen[-1] if seen else None!r}")
    print(f"[live] status={res.status} reason={res.reason} text={res.text!r}")
    assert res.status in ("available", "rejected", "timeout")
    if res.status == "available":
        assert res.text and len(res.text) <= 700 and res.reason is None
    else:
        assert res.text is None

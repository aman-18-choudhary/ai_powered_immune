"""OPT-IN live test against a LOCAL Ollama (``OLLAMA_LIVE_TESTS=1``; ``OLLAMA_URL`` default
http://127.0.0.1:11434, ``OLLAMA_MODEL`` default qwen2.5:0.5b). Runs the grounded explainer on a
digital-arrest and a benign example with the real call-guard scorer and category model, prints
what the model said and what the checks decided, and asserts only what must hold whatever the
model writes: the LLM never changes score, level or category, and accepted text passed the
grounding checks. Run with ``-s`` to see the output."""

import os

import pytest

from complaints.analysis import ComplaintAnalysisPipeline
from complaints.explain import OllamaClient

pytestmark = pytest.mark.skipif(
    os.getenv("OLLAMA_LIVE_TESTS") != "1", reason="set OLLAMA_LIVE_TESTS=1 to use a live Ollama"
)

CASES = {
    "digital_arrest": (
        "This is officer Sharma from the CBI. You are under digital arrest. Do not disconnect "
        "this video call and do not tell anyone. Transfer your money to the RBI safe account for "
        "verification or you will be arrested."
    ),
    "benign": "Hi, your electricity bill for this month is ready. You can pay it in the app.",
}


@pytest.mark.parametrize("name", list(CASES))
async def test_live_grounded_explainer(name):
    pytest.importorskip("call_guard")
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
    timeout = float(os.getenv("OLLAMA_TIMEOUT_S", "30"))  # first call loads the model
    import httpx

    seen: list[str] = []

    class Recording(httpx.AsyncHTTPTransport):
        """Keeps the raw answer of the SAME call that the grounding checks judge (for the
        report; not stored anywhere)."""

        async def handle_async_request(self, request):
            resp = await super().handle_async_request(request)
            if request.url.path == "/api/generate":
                await resp.aread()
                seen.append(httpx.Response(200, content=resp.content).json().get("response"))
            return resp

    client = OllamaClient(url, model, timeout, transport=Recording())
    st = await client.status()
    if st["status"] != "ready":
        pytest.skip(f"ollama not ready: {st}")
    complaint = {"title": "", "description": CASES[name], "category": None}
    base = await ComplaintAnalysisPipeline().analyse(complaint)
    out = await ComplaintAnalysisPipeline(explainer=client).analyse(complaint)
    llm_stage = next(
        s for s in out.extra["provenance"]["stages"] if s["stage"] == "llm_explanation"
    )
    print(
        f"\n[{name}] level={out.risk_level} score={out.risk_score} "
        f"category={out.classification} indicators={[i['code'] for i in out.indicators]}"
    )
    print(
        f"[{name}] llm status={out.capabilities['llm_explanation']} "
        f"reason={out.extra['capability_reasons'].get('llm_explanation')} stage={llm_stage}"
    )
    print(f"[{name}] llm text={out.extra['llm_explanation']!r}")
    print(f"[{name}] raw model answer judged={seen[-1] if seen else None!r}")
    print(f"[{name}] rule explanation={out.explanation!r}")
    assert (out.risk_score, out.risk_level, out.classification) == (
        base.risk_score, base.risk_level, base.classification,
    )  # fmt: skip
    assert out.explanation == base.explanation
    assert out.capabilities["llm_explanation"] in (
        "available",
        "rejected",
        "timeout",
        "skipped_no_input",
    )
    if out.capabilities["llm_explanation"] == "available":
        assert out.extra["llm_explanation"] and len(out.extra["llm_explanation"]) <= 700

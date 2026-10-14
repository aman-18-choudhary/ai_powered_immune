import asyncio
import json

import httpx
import pytest

from live_audio.explain import OllamaExplainer, build_prompt, redact, rules_explanation

FACTS = {
    "level": "HIGH",
    "score": 64,
    "indicators": [
        {"code": "DIGITAL_ARREST_PHRASE", "label": "Says you are under 'digital arrest'",
         "excerpts": ["under digital arrest"]},
        {"code": "SPOKEN_PAYMENT_ID", "label": "UPI ID mentioned", "excerpts": ["ramesh@ybl"]},
    ],
}


def transport(handler):
    return httpx.MockTransport(handler)


async def test_not_configured():
    ex = OllamaExplainer(None)
    r = await ex.explain(FACTS)
    assert r.status == "not_configured" and r.text is None
    assert await ex.probe() == "not_configured"


async def test_present():
    seen = {}

    def h(req: httpx.Request):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"response": "The caller used phrases common in scam calls. Hang up and call your bank."})

    ex = OllamaExplainer("http://ollama.local:11434", transport=transport(h))
    r = await ex.explain(FACTS)
    assert r.status == "available" and "Hang up" in r.text
    assert seen["body"]["model"] == "qwen2.5:3b" and seen["body"]["stream"] is False
    assert "ramesh@ybl" not in seen["body"]["prompt"]  # redacted
    assert "DIGITAL_ARREST_PHRASE" in seen["body"]["prompt"]
    assert await ex.probe() == "reachable"


async def test_absent_unreachable():
    def h(req):
        raise httpx.ConnectError("refused")

    ex = OllamaExplainer("http://127.0.0.1:9", transport=transport(h))
    r = await ex.explain(FACTS)
    assert r.status == "unavailable" and r.text is None
    assert await ex.probe() == "unreachable"


async def test_timeout():
    async def h(req):
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={"response": "late"})

    ex = OllamaExplainer("http://x", timeout_s=0.05, transport=transport(h))
    r = await ex.explain(FACTS)
    assert r.status == "timeout" and r.text is None


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, json={"response": "Visit http://evil.example now"}),
        httpx.Response(200, json={"response": "There is a 93% probability of fraud."}),
        httpx.Response(200, json={"response": "x" * 5000}),
        httpx.Response(500, json={"error": "boom"}),
    ],
)
async def test_garbage_rejected(resp):
    ex = OllamaExplainer("http://x", transport=transport(lambda req: resp))
    r = await ex.explain(FACTS)
    assert r.status == "error" and r.text is None


def test_prompt_contains_only_indicators_and_redacted_excerpts():
    p = build_prompt(FACTS)
    assert "ramesh@ybl" not in p and "[payment-id]" in p
    assert "HIGH" in p and "not a confirmation" in p.lower()


def test_redact():
    t = redact("call 98765 43210 or pay ramesh@ybl at sbi-kyc.in, account 123456789012")
    assert "98765" not in t and "ramesh@ybl" not in t and "sbi-kyc.in" not in t
    assert "123456789012" not in t


def test_rules_explanation_deterministic():
    a = rules_explanation(FACTS)
    assert a == rules_explanation(FACTS)
    assert "not a confirmation of fraud" in a
    assert rules_explanation({"level": None, "score": None, "indicators": []}).startswith(
        "No risk level"
    )

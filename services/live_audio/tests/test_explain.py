import asyncio
import json

import httpx
import pytest

from live_audio.explain import OllamaExplainer, build_prompt, redact, rules_explanation

FACTS = {
    "level": "HIGH",
    "score": 64,
    "indicators": [
        {
            "code": "DIGITAL_ARREST_PHRASE",
            "label": "Says you are under 'digital arrest'",
            "excerpts": ["under digital arrest"],
        },
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


GOOD = (
    "The caller says you are under digital arrest and mentioned a UPI ID. Hang up the call and "
    "do not pay."
)


def answering(text, seen=None):
    def h(req: httpx.Request):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if seen is not None:
            seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"response": text})

    return transport(h)


async def test_present():
    seen = {}
    ex = OllamaExplainer("http://ollama.local:11434", transport=answering(GOOD, seen))
    r = await ex.explain(FACTS)
    assert r.status == "available" and r.text == GOOD and r.reason is None
    body = seen["body"]
    assert body["model"] == "qwen2.5:0.5b" and body["stream"] is False
    assert body["options"] == {"temperature": 0.1, "num_predict": 120}
    assert "ramesh@ybl" not in body["prompt"]  # redacted
    assert "- The caller says you are under 'digital arrest'." in body["prompt"]
    assert 'Words heard (redacted): "under digital arrest"; "[payment-id]"' in body["prompt"]
    assert "64" not in body["prompt"] and "HIGH" not in body["prompt"]  # findings only
    assert await ex.probe() == "reachable"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # the sentence qwen2.5:0.5b produced on 2026-10-15 (finding 1), and a variant
        ("The caller is under 'digital arrest'. Hang up the call.", "role_inversion"),
        ("The caller was threatened with digital arrest and asked to pay.", "role_inversion"),
        ("The caller asked for your OTP and said you are under digital arrest.",
         "ungrounded_indicator"),
        ("This call is definitely a scam about digital arrest.", "unsupported_claim"),
        ("There is a 93% chance that this digital arrest call is fraud.", "likelihood_claim"),
        ("Please stay careful with everything you hear in general.", "not_grounded"),
        ("Visit http://evil.example now", "empty_or_too_short"),
        # qwen2.5:0.5b on 2026-10-16 echoed the prompt list back, with "You are from the CBI"
        ('1. You are under \'digital arrest\' (heard: "You are under digital arrest.")\n  2. A '
         'UPI ID was mentioned (heard: "[payment-id].")', "prompt_echo"),
        ("You are from the CBI. The caller says you are under digital arrest.", "role_inversion"),
        ("You are under digital arrest. Hang up the call.", "unsupported_claim"),
        ("The caller says you are under digital arrest. Do not tell anyone about this call.",
         "repeats_scam_instruction"),
        ("x" * 5000, "output_too_long"),
    ],
)  # fmt: skip
async def test_ungrounded_or_unsafe_output_is_rejected(text, reason):
    ex = OllamaExplainer("http://127.0.0.1:11434", transport=answering(text))
    r = await ex.explain(FACTS)
    assert (r.status, r.text, r.reason) == ("rejected", None, reason)


async def test_no_findings_no_call():
    calls = []

    def h(req):
        calls.append(req)
        return httpx.Response(200, json={"response": GOOD})

    ex = OllamaExplainer("http://127.0.0.1:11434", transport=transport(h))
    r = await ex.explain({"level": "LOW", "score": 3, "indicators": []})
    assert r.status == "skipped" and calls == []


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
        httpx.Response(500, json={"error": "boom"}),
    ],
)
async def test_garbage_is_an_error(resp):
    ex = OllamaExplainer("http://x", transport=transport(lambda req: resp))
    r = await ex.explain(FACTS)
    assert r.status == "error" and r.text is None


def test_prompt_contains_only_indicators_and_redacted_excerpts():
    p = build_prompt(FACTS)
    assert "ramesh@ybl" not in p and "[payment-id]" in p
    assert "HIGH" not in p and "not a confirmation" in p.lower()
    assert "DIGITAL_ARREST_PHRASE" not in p and "1930" in p


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

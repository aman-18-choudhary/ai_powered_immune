"""Fix round 1: script coverage, total deadline, size cap, admin-only metrics, placeholder flag."""

import asyncio
import json
import time

import httpx
import pytest

from citizen_shield.assess import assess
from citizen_shield.guard_client import CallGuardClient
from citizen_shield.policy import script_coverage
from citizen_shield.texts import ADVICE, REASON_TEXT, SUPPORTED

from .conftest import BENIGN, HDR, SCAM, Down, client

TAMIL_SCAM = "உங்களை டிஜிட்டல் கைது செய்துள்ளோம். உடனே பணம் அனுப்பவும், யாரிடமும் சொல்ல வேண்டாம், OTP பகிரவும்"
TAMIL_BENIGN = "நாளை மாலை ஐந்து மணிக்கு குடும்பத்துடன் தேநீர் அருந்தலாமா என்று கேட்டேன்"
BENGALI_SCAM = "আপনাকে ডিজিটাল অ্যারেস্ট করা হয়েছে। এখনই টাকা পাঠান এবং কাউকে বলবেন না, আপনার OTP দিন"
BENGALI_BENIGN = "কাল বিকেল পাঁচটায় পরিবারের সাথে চা খেতে যাবে কি না জানতে চাইছিলাম"
HINDI_SCAM = "आप डिजिटल अरेस्ट में हैं, तुरंत पैसे भेजें और किसी को न बताएं, अपना OTP बताएं"


def test_script_coverage():
    assert script_coverage(SCAM) == "full"
    assert script_coverage(HINDI_SCAM) == "full"
    assert script_coverage("12345 67890 \U0001f600\U0001f600 !!!") == "full"  # no letters
    assert script_coverage("") == "full"
    assert script_coverage(TAMIL_SCAM) == "partial"
    assert script_coverage(BENGALI_BENIGN) == "partial"
    assert script_coverage("مرحبا بكم") == "partial"  # Arabic
    assert script_coverage("你好世界") == "partial"  # Han
    # mixed: Tamil above / below 20% of the letters
    assert script_coverage("hello " * 3 + "வணக்கம்") == "partial"
    assert script_coverage("hello " * 30 + "வணக்கம்") == "full"


async def post(c, **body):
    r = await c.post("/assess", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("text", [TAMIL_SCAM, TAMIL_BENIGN, BENGALI_SCAM, BENGALI_BENIGN])
async def test_uncovered_script_is_never_safe_and_never_says_no_scam_signs(make_app, text):
    async with client(make_app()) as c:
        r = await post(c, message=text)
        for lang in SUPPORTED:
            loc = await post(c, message=text, lang=lang)
            assert loc["lang"] == lang
            assert "NO_RISK_INDICATORS" not in {x["code"] for x in loc["reasons"]}
    assert r["label"] != "safe" and r["uncertain"] is True and r["coverage"] == "partial"
    codes = {x["code"] for x in r["reasons"]}
    assert "COVERAGE_PARTIAL" in codes and "NO_RISK_INDICATORS" not in codes
    assert any("1930" in s for s in r["advice"]["steps"])
    assert r["score"] >= 0.35


async def test_partial_coverage_advice_is_localised_and_names_the_limit(make_app):
    async with client(make_app()) as c:
        en = await post(c, message=TAMIL_BENIGN, lang="en")
        ta = await post(c, message=TAMIL_BENIGN, lang="ta")
    assert (
        en["advice"]["key"] == "partial_coverage" and en["advice"]["steps"] != ta["advice"]["steps"]
    )
    assert "not fully checked" in en["advice"]["steps"][0]
    assert ta["lang"] == "ta"


async def test_covered_scripts_and_symbols_are_unchanged(make_app):
    async with client(make_app()) as c:
        en = await post(c, message=SCAM)
        hi = await post(c, message=HINDI_SCAM)
        num = await post(c, message="12345 6789 \U0001f600 \U0001f4b8 \U0001f4b8 \U0001f4b8")
        ok = await post(c, message=BENIGN)
    assert en["coverage"] == hi["coverage"] == num["coverage"] == ok["coverage"] == "full"
    assert en["label"] == "likely_scam" and ok["label"] == "safe"
    assert hi["label"] != "safe"


async def test_mixed_script_is_partial_only_above_twenty_percent(make_app):
    mostly_latin = (
        "Are we meeting for tea tomorrow with the whole family at five pm sharp? " * 3
    ) + "வணக்கம்"
    mostly_tamil = TAMIL_BENIGN + " tea"
    async with client(make_app()) as c:
        a = await post(c, message=mostly_latin)
        b = await post(c, message=mostly_tamil)
    assert a["coverage"] == "full" and a["label"] == "safe"
    assert b["coverage"] == "partial" and b["label"] == "suspicious"


async def test_degraded_verdict_also_carries_coverage_and_placeholder_flag(make_app):
    async with client(make_app(transport=Down(exc=httpx.ConnectError("x")))) as c:
        r = await post(c, message=TAMIL_SCAM)
        e = await post(c, message=BENIGN)
    assert r["degraded"] and r["coverage"] == "partial" and r["score_is_placeholder"] is True
    assert e["degraded"] and e["coverage"] == "full" and e["score_is_placeholder"] is True


async def test_normal_verdict_is_not_a_placeholder(make_app):
    async with client(make_app()) as c:
        r = await post(c, message=SCAM)
        t = await post(c, message=TAMIL_BENIGN)
    assert r["score_is_placeholder"] is False
    assert t["score_is_placeholder"] is True  # floored to the suspicious threshold


def test_new_texts_exist_in_every_language():
    for lang in SUPPORTED:
        assert REASON_TEXT["COVERAGE_PARTIAL"][lang].strip()
        assert ADVICE["partial_coverage"][lang]


# ---------------------------------------------------------------- guard client hardening
class _Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0.0):
        self.chunks, self.delay, self.sent = chunks, delay, 0

    async def __aiter__(self):
        for c in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += len(c)
            yield c


class StreamTransport(httpx.AsyncBaseTransport):
    def __init__(self, stream):
        self.stream = stream

    async def handle_async_request(self, request):
        return httpx.Response(200, stream=self.stream, request=request)


async def test_total_deadline_bounds_a_trickling_server():
    body = json.dumps({"score": 0.1, "reasons": [], "model_version": "clf-v1"}).encode()
    stream = _Stream([bytes([b]) for b in body], delay=0.2)  # ~ 20 s in total
    guard = CallGuardClient("http://cg", 0.5, transport=StreamTransport(stream))
    t0 = time.monotonic()
    v = await assess(guard, BENIGN, None)
    assert time.monotonic() - t0 < 1.5
    assert v.degraded and v.label == "suspicious"
    await guard.aclose()


async def test_response_size_is_capped():
    stream = _Stream([b"x" * 32_768] * 200)  # 6.5 MB
    guard = CallGuardClient("http://cg", 2.0, transport=StreamTransport(stream))
    v = await assess(guard, BENIGN, None)
    assert v.degraded and stream.sent <= 4 * 65_536
    await guard.aclose()


async def test_unexpected_transport_errors_degrade_instead_of_500(make_app):
    class Weird(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise RuntimeError("boom")

    async with client(make_app(transport=Weird())) as c:
        r = await post(c, message=BENIGN)
    assert r["degraded"] is True and "boom" not in json.dumps(r)


async def test_cancellation_is_not_swallowed():
    class Hang(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(30)

    guard = CallGuardClient("http://cg", 10.0, transport=Hang())
    task = asyncio.create_task(guard.score("hello there my friend"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await guard.aclose()


# --------------------------------------------------------------------------- /metrics
async def test_metrics_is_admin_only_through_the_gateway(make_app):
    async with client(make_app()) as c:
        for role in ("citizen", "analyst", "officer"):
            r = await c.get("/metrics", headers={**HDR, "X-Principal-Role": role})
            assert r.status_code == 403, role
        ok = await c.get("/metrics", headers={**HDR, "X-Principal-Role": "admin"})
        none = await c.get("/metrics")
    assert ok.status_code == 200 and none.status_code == 401

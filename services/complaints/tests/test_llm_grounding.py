"""Grounded LLM explanations (fake transport): the model may only rephrase the findings it is
given. Invented rationale, cues that were not detected, certainty claims and other languages are
rejected; invented numbers and links are stripped. The rule explanation stays primary and the
LLM never changes the score, level or category."""

import json

import httpx
import pytest
from test_pipeline_v2 import BARE, StubCategory, StubScorer, pipe

from complaints.explain import OllamaClient, build_prompt, ground_output, strip_invented

SAFE_ACCOUNT = (("SAFE_ACCOUNT_TRANSFER", 0.69, "asks to move money to a safe or RBI account"),
                ("AUTHORITY_IMPERSONATION", 0.4, "caller claims to be from an authority"))  # fmt: skip


def client(text, captured=None):
    def h(r):
        if captured is not None:
            captured.append(json.loads(r.content))
        return httpx.Response(200, json={"response": text})

    return OllamaClient("http://127.0.0.1:11434", "qwen2.5:0.5b", 8.0,
                        transport=httpx.MockTransport(h))  # fmt: skip


async def run(text, reasons=SAFE_ACCOUNT, captured=None):
    return await pipe(StubScorer(0.8, reasons), StubCategory("DIGITAL_ARREST", 0.7),
                      explainer=client(text, captured)).analyse(BARE)  # fmt: skip


async def test_grounded_rephrasing_is_accepted_and_labelled():
    cap: list = []
    out = await run(
        "The caller claimed to be from an authority and asked you to move money to a safe "
        "account. Follow the recommended steps.", captured=cap,
    )  # fmt: skip
    assert out.capabilities["llm_explanation"] == "available"
    part = next(p for p in out.extra["explanation_parts"] if p["source"] == "llm")
    assert part["label"] == "LLM-generated (qwen2.5:0.5b), may be imprecise"
    assert part["evidence"]["grounding_checked"] is True
    prompt = cap[0]["prompt"]
    assert "safe or RBI account" in prompt and "Recommended steps:" in prompt
    assert BARE["description"] not in prompt  # the complaint text itself is not sent
    assert cap[0]["options"] == {"temperature": 0.1, "num_predict": 120}


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # the owner's smoke-test output from qwen2.5:0.5b: fluent, plausible and wrong
        ("The demand for a safe account suggests the caller has evidence of a financial "
         "transaction.", "unsupported_claim"),
        ("The caller asked for your OTP and a safe account transfer.", "ungrounded_indicator"),
        ("This message is safe and legitimate, the safe account is real.", "unsupported_claim"),
        ("呼叫者要求您将资金转入安全账户。这是诈骗。", "wrong_language"),
        ("Please be careful with everything you receive in general.", "not_grounded"),
        ("   ", "empty_or_too_short"),
        ("It is 95% likely the safe account demand is fraud.", "likelihood_claim"),
    ],
)  # fmt: skip
async def test_ungrounded_outputs_are_rejected_and_fall_back(text, reason):
    base = await pipe(StubScorer(0.8, SAFE_ACCOUNT), StubCategory("DIGITAL_ARREST", 0.7)).analyse(
        BARE
    )
    out = await run(text)
    assert out.capabilities["llm_explanation"] == "rejected"
    assert out.extra["capability_reasons"]["llm_explanation"] == reason
    assert out.extra["llm_explanation"] is None
    assert not [p for p in out.extra["explanation_parts"] if p["source"] == "llm"]
    assert out.explanation == base.explanation  # rule explanation stays primary
    assert (out.risk_score, out.risk_level, out.classification) == (
        base.risk_score, base.risk_level, base.classification,
    )  # fmt: skip
    st = next(s for s in out.extra["provenance"]["stages"] if s["stage"] == "llm_explanation")
    assert st["status"] == "rejected" and st["reason"] == reason
    assert any(u["stage"] == "llm_explanation" for u in out.extra["provenance"]["unavailable"])


async def test_invented_numbers_and_links_are_stripped():
    out = await run(
        "The caller claimed authority and asked to move money to a safe account; call "
        "9876543210 or visit https://help.example.top for help."
    )
    assert out.capabilities["llm_explanation"] == "available"
    txt = out.extra["llm_explanation"]
    assert "9876543210" not in txt and "example.top" not in txt and "safe account" in txt


async def test_timeout_is_recorded_in_provenance():
    def slow(r):
        raise httpx.ReadTimeout("slow", request=r)

    c = OllamaClient("http://127.0.0.1:11434", "qwen2.5:0.5b", 8.0,
                     transport=httpx.MockTransport(slow))  # fmt: skip
    out = await pipe(StubScorer(0.8, SAFE_ACCOUNT), explainer=c).analyse(BARE)
    st = next(s for s in out.extra["provenance"]["stages"] if s["stage"] == "llm_explanation")
    assert st["status"] == "timeout" and out.extra["llm_explanation"] is None


def test_no_findings_prompt_and_helpers():
    p = build_prompt("ignored text 9876543210", {"indicators": [], "risk_level": "LOW"})
    assert "No scam cue was detected" in p and "9876543210" not in p
    assert ground_output("No scam cue was detected by the checks here.", p, {}) is None
    assert ground_output("The caller threatened arrest.", p, {}) == "ungrounded_indicator"
    assert strip_invented("call 12345678 now", "prompt") == "call now"


def test_defaults_are_the_small_local_model():
    from complaints.config import Settings
    from complaints.explain import DEFAULT_MODEL, DEFAULT_TIMEOUT_S

    assert DEFAULT_MODEL == "qwen2.5:0.5b" and DEFAULT_TIMEOUT_S == 8.0
    assert Settings.__dataclass_fields__["ollama_model"].default == "qwen2.5:0.5b"


async def test_role_inversion_is_rejected():
    reasons = (("DIGITAL_ARREST_PHRASE", 0.69, "caller says the victim is under 'digital arrest'"),)
    out = await run("The caller is under digital arrest and wants money.", reasons=reasons)
    assert out.capabilities["llm_explanation"] == "rejected"
    assert out.extra["capability_reasons"]["llm_explanation"] == "role_inversion"


async def test_copied_guidance_sentence_is_not_a_likelihood_claim_and_cut_text_is_tidied():
    # no category: the steps given to the model are the common ones, including the helpline one
    out = await pipe(StubScorer(0.5, SAFE_ACCOUNT), StubCategory("OTHER", 0.7), explainer=client(
        
        "The caller claimed authority and asked to move money to a safe account. If money has "
        "been lost, call the national cyber-crime helpline 1930 immediately and report at [link]; "
        "the sooner it is reported the better the chance of a freeze. The caller claimed "
        "authority and asked to move money to a safe account. And then the"
    )).analyse({**BARE, "category": None})  # fmt: skip
    assert out.capabilities["llm_explanation"] == "available"
    txt = out.extra["llm_explanation"]
    assert "[link]" not in txt and txt.count("safe account") == 1 and txt.endswith(".")


async def test_no_findings_means_no_llm_call():
    calls: list = []
    out = await pipe(StubScorer(0.1, ()), StubCategory("OTHER", 0.7),
                     explainer=client("anything", calls)).analyse(BARE)  # fmt: skip
    assert calls == [] and out.capabilities["llm_explanation"] == "skipped_no_input"
    assert out.extra["capability_reasons"]["llm_explanation"] == "no_findings_to_rephrase"


def test_tidy_list_echo():
    from complaints.explain import tidy

    raw = "1. The text matches known scripts.\n2. The caller demands a safe account.\nRecommended steps:\n- Do x."
    assert tidy(raw) == "The text matches known scripts. The caller demands a safe account."


def test_complaints_ollama_url_wins(monkeypatch):
    from complaints.config import Settings

    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("COMPLAINTS_OLLAMA_URL", "http://127.0.0.1:11434/")
    assert Settings.from_env().ollama_url == "http://127.0.0.1:11434"
    monkeypatch.delenv("COMPLAINTS_OLLAMA_URL")
    assert Settings.from_env().ollama_url == "http://127.0.0.1:1"
    monkeypatch.delenv("OLLAMA_URL")
    s = Settings.from_env()
    assert s.ollama_url is None and s.ollama_model == "qwen2.5:0.5b" and s.ollama_timeout_s == 8.0


@pytest.mark.parametrize(
    "text",
    [
        # produced by qwen2.5:0.5b on 2026-10-15 and accepted by the old regex (finding 1)
        "The message mentions digital arrest, and the caller is under 'digital arrest'.",
        "The caller is under \"Digital Arrest\" and asks for money.",
        "The caller was threatened with digital arrest and asked to pay.",
    ],
)
async def test_role_inversion_variants_are_rejected_and_rules_text_is_kept(text):
    reasons = (("DIGITAL_ARREST_PHRASE", 0.69, "caller says the victim is under 'digital arrest'"),)
    base = await pipe(StubScorer(0.8, reasons), StubCategory("DIGITAL_ARREST", 0.7)).analyse(BARE)
    out = await run(text, reasons=reasons)
    assert out.capabilities["llm_explanation"] == "rejected"
    assert out.extra["capability_reasons"]["llm_explanation"] == "role_inversion"
    assert out.extra["llm_explanation"] is None and out.explanation == base.explanation
    st = next(s for s in out.extra["provenance"]["stages"] if s["stage"] == "llm_explanation")
    assert st["status"] == "rejected" and st["reason"] == "role_inversion"


async def test_correct_digital_arrest_wording_is_accepted():
    reasons = (("DIGITAL_ARREST_PHRASE", 0.69, "caller says the victim is under 'digital arrest'"),)
    out = await run("The caller says you are under 'digital arrest'. This is a known scam cue.",
                    reasons=reasons)  # fmt: skip
    assert out.capabilities["llm_explanation"] == "available", out.extra["capability_reasons"]

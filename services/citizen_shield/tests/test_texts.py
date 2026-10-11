import re
from pathlib import Path

import call_guard
from call_guard import rules

from citizen_shield.policy import MAX_CHARS
from citizen_shield.texts import (
    ADVICE,
    GENERIC_REASON,
    LANGUAGE_NAMES,
    REASON_TEXT,
    SUPPORTED,
    TOO_LONG_NOTICE,
)


def guard_codes() -> set[str]:
    """Every reason code call-guard can emit: the rule spec keys plus any ``code="X"`` literal in
    its package source (classifier and no-indicator reasons live in model.py)."""
    found = set(rules._SPECS)
    for f in Path(call_guard.__file__).parent.glob("*.py"):
        found |= set(re.findall(r"""code\s*=\s*["']([A-Z][A-Z_]+)["']""", f.read_text()))
    return found


def test_guard_codes_are_discovered():
    assert {
        "DIGITAL_ARREST_PHRASE",
        "NO_RISK_INDICATORS",
        "SCRIPT_CLASSIFIER_MATCH",
    } <= guard_codes()


def test_every_call_guard_reason_code_has_text_in_every_language():
    missing = [
        (code, lang)
        for code in sorted(guard_codes())
        for lang in SUPPORTED
        if not REASON_TEXT.get(code, {}).get(lang, "").strip()
    ]
    assert missing == []


def test_no_stale_codes_and_all_tables_complete():
    assert (
        set(REASON_TEXT) - {"COVERAGE_PARTIAL"} <= guard_codes()
    )  # a removed rule leaves no orphan text
    for table in (GENERIC_REASON, TOO_LONG_NOTICE, LANGUAGE_NAMES):
        assert set(table) == set(SUPPORTED) and all(v.strip() for v in table.values())
    for key in ("likely_scam", "suspicious", "safe", "no_input", "degraded", "partial_coverage"):
        for lang in SUPPORTED:
            steps = ADVICE[key][lang]
            assert steps and all(s.strip() for s in steps)
        assert len({len(ADVICE[key][lang]) for lang in SUPPORTED}) == 1  # same steps per language


def test_degraded_advice_warns_against_sharing_credentials_in_every_language():
    for lang in SUPPORTED:
        text = " ".join(ADVICE["degraded"][lang])
        assert "OTP" in text and "1930" in text


def test_truncation_notice_matches_the_cap():
    assert MAX_CHARS == 4000 and "4,000" in TOO_LONG_NOTICE["en"]

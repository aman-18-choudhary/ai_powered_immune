from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scam_contracts.geo import CITY_CODES, STATE_CODES
from scam_contracts.models import Complaint

T = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def make(**over):
    base = dict(complaint_id="cmp-00000001", ts=T, kind="scam_call", city_code="MUM")
    base.update(over)
    return Complaint(**base)


def test_roundtrip_and_additive_defaults():
    c = make(amount_bucket="10k-100k", case_ref="case:42")
    again = Complaint.model_validate_json(c.model_dump_json())
    assert again == c
    assert make().amount_bucket is None and make().case_ref is None


def test_state_code_alone_is_valid_and_location_is_required():
    assert make(city_code=None, state_code="MH").state_code == "MH"
    with pytest.raises(ValidationError):
        make(city_code=None)


@pytest.mark.parametrize(
    "over",
    [
        {"city_code": "Mumbai"},  # free text is not an enum value
        {"city_code": "mum"},
        {"city_code": None, "state_code": "Maharashtra"},
        {"city_code": None, "state_code": "XX"},
        {"kind": "other"},
        {"amount_bucket": "5000"},
        {"complaint_id": "short"},
        {"complaint_id": "has space in it"},
        {"complaint_id": "x" * 65},
        {"case_ref": "bad ref"},
        {"ts": datetime(2026, 3, 10, 12, 0)},  # naive
    ],
)
def test_rejects_free_text_and_malformed(over):
    with pytest.raises(ValidationError):
        make(**over)


def test_no_pii_style_fields_accepted_as_extras_in_the_model_dump():
    # the model has exactly these fields; a producer cannot smuggle a phone number in
    assert set(Complaint.model_fields) == {
        "complaint_id", "ts", "kind", "city_code", "state_code", "amount_bucket", "case_ref",
    }  # fmt: skip


def test_code_enums_are_unique_and_upper_case():
    for codes in (CITY_CODES, STATE_CODES):
        assert len(set(codes)) == len(codes) and all(c.isupper() for c in codes)

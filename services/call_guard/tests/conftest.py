from datetime import UTC, datetime

import pytest
from scam_contracts.models import CallEvent


@pytest.fixture
def make_event():
    counter = {"n": 0}

    def _make(text: str, call_id: str = "c1", ts: datetime | None = None, lang: str = "en"):
        counter["n"] += 1
        return CallEvent(
            call_id=call_id,
            idempotency_key=f"{call_id}:{counter['n']}",
            victim_token="v1",
            caller_number_hash="h1",
            ts=ts or datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            transcript_chunk=text,
            channel="video",
            lang=lang,
        )

    return _make

"""Strip exact amounts from the services' free-text reason details.

The txn-guard policy texts embed the exact amount ("amount (Rs 16,000)"). Stream events and
snapshots may carry amounts only as buckets, so every reason ``detail`` passes through
``sanitise_detail`` before it enters an event. Exact INR stays in the officer/analyst REST holds
view (``amount_inr``, a decimal string).
"""

from __future__ import annotations

import re

WITHHELD = "[amount withheld]"

# currency marker followed by a number with Indian or Western grouping, optional unit word
_CURRENCY = re.compile(
    r"(?:₹|\bRs\.?|\bINR\b)\s*\d[\d,_]*(?:\.\d+)?(?:\s*(?:lakhs?|crores?|lacs?|k|m|cr|l)\b)?",
    re.IGNORECASE,
)
# grouped numbers (1,23,456 / 12,000) and bare 5+ digit numbers; an antibody short id is kept
_GROUPED = re.compile(r"(?<!antibody )\b\d{1,3}(?:[,_]\d{2,3})+(?:\.\d+)?\b")
_LONG = re.compile(r"(?<!antibody )\b\d{5,}(?:\.\d+)?\b")
# "about 123456x typical": a multiple that large is an amount in disguise
_MULTIPLE = re.compile(r"\b\d{4,}x\b")


def sanitise_detail(text: str, bucket: str | None = None) -> str:
    """Replace amounts in ``text`` by the amount bucket label (or a fixed phrase)."""
    repl = f"INR {bucket}" if bucket else WITHHELD
    out = _CURRENCY.sub(repl, text)
    out = _GROUPED.sub(repl, out)
    out = _LONG.sub(repl, out)
    return _MULTIPLE.sub(">999x", out)

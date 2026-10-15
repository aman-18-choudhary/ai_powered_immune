"""Provenance of analysis output: which kind of component produced each indicator, explanation
part and recommended action, and what evidence it points to.

Vocabulary (``source``, fixed and small):

* ``rule``: deterministic, hand-written logic (call-guard rule cues, URL lexical heuristics,
  entity format checks, the risk formula and the explanation templates).
* ``ml``: a trained statistical model (call-guard ``clf-v1`` script classifier, the complaint
  category model ``category-v1``). Both were trained on synthetic / simulator data only.
* ``llm``: a local language model (Ollama). Used for additional explanation wording only; never
  for the score, level, category or indicators.
* ``static_guidance``: authored advice keyed by category and level, not a finding.

``method`` names the component (``call_guard.rules``, ``call_guard.clf-v1``, ``category-v1``,
``url_heuristics``, ``entity_checks``, ``risk_formula``, ``explanation_template``,
``static_guidance_table``, ``ollama:<model>``). ``evidence`` points to where the output came
from: a sentence-level span of the analysed text (offsets into the named field and a short
snippet, never the whole text), a field name, or ``whole_text`` for a model that scores the text
as a whole and cannot point to a span.

Rows stored before provenance existed get a provenance derived from what they recorded
(``derived_from_legacy: true``); when nothing can be derived they are marked ``legacy``.
"""

import re
from typing import Any

SOURCES = ("rule", "ml", "llm", "static_guidance")
PROVENANCE_SCHEMA = 1
SNIPPET_MAX = 160
CLASSIFIER_CODE = "SCRIPT_CLASSIFIER_MATCH"
RULES_METHOD = "call_guard.rules"
URL_METHOD = "url_heuristics"
ENTITY_METHOD = "entity_checks"
GUIDANCE_METHOD = "static_guidance_table"
FORMULA_METHOD = "risk_formula"
TEMPLATE_METHOD = "explanation_template"
CATEGORY_METHOD = "category-v1"

# the same sentence split as call_guard.rules (applied to the raw text so offsets stay exact)
_SENT_SPLIT = re.compile(r"(?<=[.?!।])\s+|\n+")


def classifier_method(version: str | None) -> str:
    return f"call_guard.{version or 'clf-v1'}"


def snippet(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= SNIPPET_MAX else s[: SNIPPET_MAX - 1].rstrip() + "…"


class TextLayout:
    """The analysed text is ``title`` + newline + ``description`` (either may be absent); maps an
    offset in it back to the field it came from."""

    def __init__(self, title: str | None, description: str | None, max_chars: int,
                 description_field: str = "description") -> None:  # fmt: skip
        parts: list[tuple[str, str]] = []
        if title:
            parts.append(("title", title))
        if description:
            parts.append((description_field, description))
        self.fields: list[tuple[str, int, int]] = []
        pos, chunks = 0, []
        for i, (name, val) in enumerate(parts):
            if i:
                chunks.append("\n")
                pos += 1
            self.fields.append((name, pos, pos + len(val)))
            chunks.append(val)
            pos += len(val)
        self.text = "".join(chunks)[:max_chars]
        self.description_field = description_field

    @property
    def field_label(self) -> str:
        return "+".join(n for n, _, _ in self.fields) or "text"

    def sentences(self) -> list[tuple[int, int]]:
        out, start = [], 0
        for m in _SENT_SPLIT.finditer(self.text):
            if m.start() > start:
                out.append((start, m.start()))
            start = m.end()
        if start < len(self.text):
            out.append((start, len(self.text)))
        return out

    def span_evidence(self, start: int, end: int) -> dict[str, Any]:
        for name, a, b in self.fields:
            if a <= start < b:
                return {
                    "type": "text_span", "field": name, "start": start - a,
                    "end": min(end, b) - a, "snippet": snippet(self.text[start:end]),
                    "granularity": "sentence",
                }  # fmt: skip
        return {"type": "text_span", "field": self.field_label, "start": start, "end": end,
                "snippet": snippet(self.text[start:end]), "granularity": "sentence"}  # fmt: skip


def locate_rule_cues(layout: TextLayout, codes: set[str]) -> dict[str, dict[str, Any]]:
    """For each call-guard rule code, the first sentence in which the rules find it. The rules
    combine cues per sentence (max over sentences), so every code they report is found in at least
    one sentence; a code that cannot be located (e.g. the rule module is not importable) is left
    out and the caller records ``span: null``."""
    if not codes:
        return {}
    try:
        from call_guard.rules import analyse as rules_analyse
    except Exception:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for a, b in layout.sentences():
        if len(found) == len(codes):
            break
        try:
            _, reasons, _ = rules_analyse(layout.text[a:b])
        except Exception:
            return found
        for r in reasons:
            if r.code in codes and r.code not in found:
                found[r.code] = layout.span_evidence(a, b)
    return found


def tag_call_guard(
    reasons: list[Any], layout: TextLayout, text_version: str, humanise: Any
) -> list[dict[str, Any]]:
    codes = {r.code for r in reasons if r.code not in (CLASSIFIER_CODE, "NO_RISK_INDICATORS")}
    spans = locate_rule_cues(layout, codes)
    out = []
    for r in reasons:
        if r.code == "NO_RISK_INDICATORS":
            continue
        ind: dict[str, Any] = {
            "code": r.code, "weight": r.weight, "text": r.detail or humanise(r.code),
            "stage": "call_guard",
        }  # fmt: skip
        if r.code == CLASSIFIER_CODE:
            ind.update(
                source="ml", method=classifier_method(text_version),
                evidence={"type": "whole_text", "field": layout.field_label,
                          "note": "the classifier scores the whole text; it does not point to "
                          "a span"},
            )  # fmt: skip
        else:
            ind.update(
                source="rule", method=RULES_METHOD,
                evidence=spans.get(r.code) or {"type": "text_span", "field": layout.field_label,
                                               "span": None, "note": "span not located"},
            )  # fmt: skip
        out.append(ind)
    return out


def tag_url(ind: dict[str, Any], field: str, host: str | None) -> dict[str, Any]:
    return {
        **ind, "stage": "url_heuristics", "source": "rule", "method": URL_METHOD,
        "evidence": {"type": "field", "field": field, "host": host},
    }  # fmt: skip


ENTITY_FIELDS = {"phone": "suspect_phone", "upi_id": "upi_id", "email": "suspect_email"}


def tag_entities(entities: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    """``names`` renames the evidence field (``POST /analyze`` uses ``phone``/``email``)."""
    names = names or {}
    for c in entities.get("checks", []):
        c["source"] = "rule"
        c["method"] = ENTITY_METHOD
        f = ENTITY_FIELDS.get(c.get("field"), c.get("field"))
        c["evidence"] = {"type": "field", "field": names.get(f, f)}
    return entities


def tag_guidance(actions: list[dict[str, Any]], category: str | None, level: str | None):
    return [
        {**a, "source": "static_guidance", "method": GUIDANCE_METHOD,
         "evidence": {"type": "keyed_by", "category": category, "risk_level": level}}
        for a in actions
    ]  # fmt: skip


# ----------------------------------------------------------------------------- legacy rows
_LEGACY_STAGE = {"call_guard": "call_guard", "url_heuristics": "url_heuristics"}


def legacy_indicator(ind: dict[str, Any]) -> dict[str, Any]:
    """An indicator stored before provenance: derive source/method from the old ``source``."""
    if ind.get("source") in SOURCES and "method" in ind:
        return ind
    old = ind.get("source")
    out = {**ind, "stage": _LEGACY_STAGE.get(old, old), "evidence": {"type": "not_recorded"}}
    if old == "call_guard":
        if ind.get("code") == CLASSIFIER_CODE:
            out.update(source="ml", method=classifier_method(None))
        else:
            out.update(source="rule", method=RULES_METHOD)
    elif old == "url_heuristics":
        out.update(source="rule", method=URL_METHOD)
    else:
        out.update(source="legacy", method="unknown")
    out["derived_from_legacy"] = True
    return out


def legacy_action(a: dict[str, Any]) -> dict[str, Any]:
    if "method" in a:
        return a
    return {**a, "source": a.get("source") or "static_guidance", "method": GUIDANCE_METHOD,
            "derived_from_legacy": True}  # fmt: skip


def legacy_provenance(row: dict[str, Any]) -> dict[str, Any]:
    """Provenance for a row stored before it existed, from ``pipeline_version`` and
    ``capabilities``. Marked ``legacy`` when the pipeline version is not recognised."""
    pv = str(row.get("pipeline_version") or "")
    caps = row.get("capabilities") or {}
    parts = pv.split("+")
    base = {"schema": PROVENANCE_SCHEMA, "derived_from_legacy": True}
    if parts[0] not in ("complaints-text-v1", "complaints-analysis-v2"):
        return {**base, "legacy": True,
                "note": "stored before provenance was recorded; it cannot be derived"}  # fmt: skip
    text_v = parts[1] if len(parts) > 1 else None
    cat_v = parts[2] if len(parts) > 2 and not parts[2].startswith("category-unavailable") else None
    ran, unavailable = [], []
    txt = caps.get("text_risk_scorer")
    if txt == "available":
        ran.append("text_rules")
        if text_v and not text_v.startswith("rules-"):
            ran.append("script_classifier")
    elif txt:
        unavailable.append({"stage": "text_rules", "reason": txt})
    cat = caps.get("category_classifier")
    if cat == "available":
        ran.append("category_classifier")
    elif cat:
        unavailable.append({"stage": "category_classifier", "reason": cat})
    if caps.get("url_heuristics") == "available":
        ran.append("url_heuristics")
    if caps.get("entity_format_checks") == "available":
        ran.append("entity_checks")
    if caps.get("llm_explanation") == "available":
        ran.append("llm_explanation")
    ran.append("static_guidance")
    return {
        **base, "legacy": False, "pipeline": parts[0], "ran": ran, "unavailable": unavailable,
        "skipped": [],
        "model_versions": {
            "call_guard": text_v, "category": cat_v, "llm": None,
        },
        "note": "derived from the stored pipeline version and capabilities; per-item evidence "
        "was not recorded for this analysis",
    }  # fmt: skip

"""Complaint analysis: the ``AnalysisService`` protocol, the v1 text-only implementation
(``CallGuardTextAnalysis``, kept unchanged) and the v2 pipeline (``ComplaintAnalysisPipeline``,
defined at the end of this module and documented in the README).

``CallGuardTextAnalysis`` scores the complaint text (title + description) with the in-process
call-guard scorer (rules + TF-IDF/logistic classifier trained on simulator and authored data). The
0-100 ``risk_score`` is ``round(score * 100)`` of an UNCALIBRATED heuristic ordering, not a
likelihood; bands: LOW < 35, MEDIUM 35-69, HIGH >= 70. Nothing else is analysed: no category
classifier, no LLM explanation, no evidence-file analysis (all reported as ``unavailable``).
Optional complaint fields are never used as fraud evidence. If the scorer cannot be loaded or
fails the status is UNAVAILABLE with no level (never LOW).
"""

import asyncio
import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("complaints.analysis")

MAX_TEXT_CHARS = 4000  # call-guard's own message cap
SCORE_SEMANTICS = "uncalibrated_heuristic"
PIPELINE = "complaints-text-v1"

CAPABILITIES_NONE: dict[str, str] = {
    "text_risk_scorer": "unavailable",
    "category_classifier": "unavailable",
    "llm_explanation": "unavailable",
    "evidence_content_analysis": "unavailable",
}


def band(risk_score: int) -> str:
    return "HIGH" if risk_score >= 70 else "MEDIUM" if risk_score >= 35 else "LOW"


@dataclass(frozen=True)
class AnalysisOutcome:
    status: str  # COMPLETED | PARTIAL | UNAVAILABLE
    pipeline_version: str
    capabilities: dict[str, str]
    risk_score: int | None
    risk_level: str | None
    indicators: list[dict[str, Any]]
    recommended_actions: list[dict[str, Any]]
    limitations: list[str]
    classification: str | None = None
    confidence: float | None = None
    explanation: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AnalysisService(Protocol):
    async def analyse(self, complaint: dict[str, Any]) -> AnalysisOutcome: ...


_BASE_LIMITS = [
    "The risk score is an uncalibrated heuristic ordering of the complaint text, not a likelihood.",
    "The scorer was built for digital-arrest call scripts and trained on simulator and authored "
    "data; it has not been validated on real complaints. A narrative written from the victim's "
    "point of view can score lower than a transcript of what the caller said.",
    "Only the title and description text are analysed. Optional fields (phone, URL, amount, "
    "location) and uploaded evidence files are not used.",
    "Recommended actions are general static guidance, not a finding about this complaint.",
]

_COMMON = [
    "Do not send more money or share any OTP, PIN, CVV or password with anyone.",
    "If money has been lost, call the national cyber-crime helpline 1930 immediately and report "
    "at cybercrime.gov.in; the sooner it is reported the better the chance of a freeze.",
    "Tell your bank straight away so it can block the card, UPI handle or transfer.",
    "Keep screenshots, call logs, messages and transaction references; do not delete them.",
]
_BY_CATEGORY: dict[str, list[str]] = {
    "DIGITAL_ARREST": [
        "No agency can place you under 'digital arrest' or demand money over a call or video "
        "call. End the call and do not stay on video.",
    ],
    "UPI_FRAUD": [
        "Never approve a UPI collect request or scan a QR code to receive money.",
    ],
    "PHISHING": [
        "Do not click links or open attachments from the message; go to the bank or service "
        "through its official app or website.",
        "Change the password of any account whose details you entered.",
    ],
    "OTP_SCAM": [
        "A bank or any service will never ask you for an OTP; treat every request as fraud."
    ],
    "BANK_FRAUD": [
        "Ask your bank to block the card or account and to give you a written reference."
    ],
    "INVESTMENT_SCAM": [
        "Stop all transfers. Fees demanded to 'release' or 'withdraw' profits are a common sign "
        "of fraud.",
    ],
    "JOB_SCAM": ["Genuine employers do not ask candidates for fees or deposits before a job."],
    "SOCIAL_MEDIA_FRAUD": [
        "Report and block the account on the platform and do not continue the conversation.",
    ],
    "OTHER": [],
}
_HIGH_FIRST = "Treat this as urgent: act on the steps below now."


def static_guidance(category: str | None, level: str | None) -> list[dict[str, str]]:
    """Static, labelled advice keyed by category and level. Authored text: it needs legal and
    domain review before any production use (see README)."""
    texts: list[str] = []
    if level in ("HIGH", "CRITICAL"):
        texts.append(_HIGH_FIRST)
    texts.extend(_BY_CATEGORY.get(category or "OTHER", []))
    texts.extend(_COMMON)
    return [{"text": t, "source": "static_guidance"} for t in texts]


def _has_reason(reasons: Any) -> bool:
    return any(getattr(r, "code", None) not in (None, "NO_RISK_INDICATORS") for r in reasons)


def _explained_score(scorer: Any, text: str) -> Any:
    """call-guard score where every point has a listed reason (the classifier counts only when it
    adds SCRIPT_CLASSIFIER_MATCH). Scorers without the option (test doubles) are called as is."""
    if "require_explained" in inspect.signature(scorer.score_text).parameters:
        return scorer.score_text(text, require_explained=True)
    return scorer.score_text(text)


def _humanise(code: str) -> str:
    return code.replace("_", " ").capitalize()


def _load_call_guard() -> Any:
    from call_guard.model import Scorer, load_classifier

    return Scorer(load_classifier())


class CallGuardTextAnalysis:
    def __init__(self, scorer: Any = None, scorer_loader: Callable[[], Any] | None = None) -> None:
        self._scorer = scorer
        self._loader = scorer_loader or _load_call_guard
        self._failed: str | None = None
        self._lock = asyncio.Lock()

    @property
    def importable(self) -> bool:
        import importlib.util

        return self._scorer is not None or importlib.util.find_spec("call_guard") is not None

    async def _get_scorer(self) -> Any | None:
        if self._scorer is not None:
            return self._scorer
        if self._failed:
            return None
        async with self._lock:
            if self._scorer is None and not self._failed:
                try:
                    self._scorer = await asyncio.to_thread(self._loader)
                except Exception as exc:  # ImportError, missing artifact, sklearn mismatch...
                    self._failed = type(exc).__name__
                    log.warning("text scorer unavailable: %s", self._failed)
        return self._scorer

    def _unavailable(self, category: str | None) -> AnalysisOutcome:
        return AnalysisOutcome(
            status="UNAVAILABLE",
            pipeline_version=PIPELINE,
            capabilities=dict(CAPABILITIES_NONE),
            risk_score=None,
            risk_level=None,
            indicators=[],
            recommended_actions=static_guidance(category, None),
            limitations=["The text risk scorer is unavailable, so no risk assessment was made.",
                         *_BASE_LIMITS[2:]],
        )  # fmt: skip

    async def analyse(self, complaint: dict[str, Any]) -> AnalysisOutcome:
        category = complaint.get("category")
        scorer = await self._get_scorer()
        if scorer is None:
            return self._unavailable(category)
        text = f"{complaint.get('title', '')}\n{complaint.get('description', '')}"[:MAX_TEXT_CHARS]
        try:
            score, reasons, model_version = await asyncio.to_thread(_explained_score, scorer, text)
        except Exception as exc:
            log.warning("text scoring failed: %s", type(exc).__name__)
            return self._unavailable(category)
        risk = max(0, min(100, round(float(score) * 100)))
        level = band(risk)
        indicators = [
            {
                "code": r.code, "weight": r.weight, "text": r.detail or _humanise(r.code),
                "source": "call_guard",
            }
            for r in reasons
            if r.code != "NO_RISK_INDICATORS"
        ]  # fmt: skip
        limitations = list(_BASE_LIMITS)
        if risk > 0 and not indicators:
            limitations.append(
                "The score comes from the text classifier alone: no specific scam cue was matched."
            )
        caps = dict(CAPABILITIES_NONE)
        caps["text_risk_scorer"] = "available"
        return AnalysisOutcome(
            status="COMPLETED",
            pipeline_version=f"{PIPELINE}+{model_version}",
            capabilities=caps,
            risk_score=risk,
            risk_level=level,
            indicators=indicators,
            recommended_actions=static_guidance(category, level),
            limitations=limitations,
        )


# =============================================================================================
# Pipeline v2
# =============================================================================================
PIPELINE_V2 = "complaints-analysis-v2"
RISK_FORMULA = (
    "risk = min(100, max(text_score, transcript_score) + round(25 * "
    "noisy_or(url_indicator_weights))); text_score = round(100 * call-guard score of the title "
    "and description, counting only scored parts with a listed reason); transcript_score = the "
    "same for audio evidence transcripts when audio evidence was transcribed; the predicted "
    "category is a model guess and is NOT part of the score; HIGH or CRITICAL needs at least one "
    "rule-based indicator (a score from the ML classifier alone is capped at MEDIUM); terms of "
    "unavailable stages are left out; if the text scorer did not run or an audio file could not be "
    "transcribed, a result that would be LOW is withheld (risk_score and risk_level null)"
)
BANDS_V2 = {"LOW": "0-34", "MEDIUM": "35-69", "HIGH": "70-89", "CRITICAL": "90-100"}
URL_ADJUSTMENT_MAX = 25
UNAVAILABLE_STATES = ("unavailable", "timeout", "error", "rejected")
_NOT_RUN = ("unavailable", "not_configured", "rejected", "timeout", "error")
MIN_WORDS = 4  # below this the text models are not run (call-guard's classifier needs 4 words)
SKIPPED_SHORT = "skipped_insufficient_input"
_WORD = re.compile(r"\w+")
_FIELD_NAMES = {
    "suspect_phone": "phone number", "suspicious_url": "URL", "upi_id": "UPI id",
    "amount": "amount", "city": "location", "suspect_email": "email",
}  # fmt: skip


def band_v2(risk_score: int) -> str:
    return (
        "CRITICAL" if risk_score >= 90
        else "HIGH" if risk_score >= 70
        else "MEDIUM" if risk_score >= 35
        else "LOW"
    )  # fmt: skip


def stored_level(level: str | None) -> str | None:
    """The level written to ``analyses.risk_level``. Since migration 6 the column allows CRITICAL,
    so the exact level is stored (before it, CRITICAL was stored as HIGH; those rows are
    backfilled by the migration and ``detail.risk_level`` still holds the exact level)."""
    return level


def _attachments(evidence: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], set[str]]:
    """Files whose content was NOT analysed: images and PDFs grouped by type, and each audio file
    that has no transcript, with the precise reason (``complaints.transcribe.STATUS_TEXT``)."""
    from .transcribe import STATUS_TEXT, TRANSCRIBED

    kinds: dict[tuple[str, str], int] = {}
    seen: set[str] = set()
    audio: list[dict[str, Any]] = []
    for e in evidence or []:
        mime = str(e.get("mime_type") or "unknown")[:60]
        kind = (
            "audio" if mime.startswith("audio/")
            else "screenshot_or_image" if mime.startswith("image/")
            else "document" if mime == "application/pdf"
            else "other"
        )  # fmt: skip
        seen.add(kind)
        if kind == "audio":
            st = (e.get("transcription") or {}).get("status") or "not_attempted"
            if st not in TRANSCRIBED:
                audio.append({
                    "type": "audio", "mime_type": mime, "count": 1, "analysed": False,
                    "reason": STATUS_TEXT.get(st, st), "status": st,
                    "evidence_id": e.get("evidence_id"),
                })  # fmt: skip
            continue
        kinds[(kind, mime)] = kinds.get((kind, mime), 0) + 1
    reasons = {
        "screenshot_or_image": "no OCR engine is installed",
        "document": "no document text extraction is implemented",
        "other": "this file type is not analysed",
    }
    out = [
        {"type": k, "mime_type": m, "count": n, "analysed": False, "reason": reasons[k]}
        for (k, m), n in kinds.items()
    ] + audio
    out.sort(key=lambda x: (x["type"], x["mime_type"]))
    return out, seen


_STAGE_STATUS = {
    "transcribed": "ran", "truncated": "ran", "engine_not_configured": "not_configured",
    "model_not_available_offline": "unavailable", "timeout": "timeout",
    "decode_failed": "error", "decode_unavailable": "unavailable",
    "unsupported_format": "unavailable", "budget_exhausted": "skipped",
    "not_attempted_limit": "skipped", "engine_error": "error", "not_attempted": "skipped",
}  # fmt: skip


def _audio_summary(e: dict[str, Any]) -> dict[str, Any]:
    from .transcribe import STATUS_TEXT

    tr = e.get("transcription") or {}
    st = tr.get("status") or "not_attempted"
    return {
        "evidence_id": e.get("evidence_id"), "mime_type": e.get("mime_type"), "status": st,
        "reason": tr.get("reason") or st, "message": STATUS_TEXT.get(st, st),
        "engine": tr.get("engine"), "model": tr.get("model"), "language": tr.get("language"),
        "duration_s": tr.get("duration_s"), "transcribed_s": tr.get("transcribed_s"),
        "truncated": bool(tr.get("truncated")), "segment_count": len(tr.get("segments") or []),
        "reused": bool(tr.get("reused")),
    }  # fmt: skip


class ComplaintAnalysisPipeline(CallGuardTextAnalysis):
    """v2: text scorer + category model + offline URL/entity signals + explanations.

    Every stage reports its own capability status; one stage failing never blocks another and a
    failure is never converted into LOW or BENIGN."""

    def __init__(
        self,
        scorer: Any = None,
        scorer_loader: Callable[[], Any] | None = None,
        *,
        category_model: Any = None,
        category_loader: Callable[[], Any] | None = None,
        explainer: Any = None,
    ) -> None:
        super().__init__(scorer=scorer, scorer_loader=scorer_loader)
        self._cat = category_model
        self._cat_loader = category_loader
        self._cat_reason: str | None = None
        self._cat_tried = category_model is not None
        self._cat_lock = asyncio.Lock()
        self._explainer = explainer

    # -- category model (lazy, cached, failure is reported not hidden)
    async def _get_category_model(self) -> Any | None:
        if self._cat is not None or self._cat_tried:
            return self._cat
        async with self._cat_lock:
            if self._cat is None and not self._cat_tried:
                try:
                    if self._cat_loader is not None:
                        self._cat = await asyncio.to_thread(self._cat_loader)
                        self._cat_reason = None if self._cat is not None else "load_failed"
                    else:
                        from .ml.category_model import load_with_status

                        res = await asyncio.to_thread(load_with_status)
                        self._cat, self._cat_reason = res.model, res.reason
                except Exception as exc:
                    self._cat_reason = type(exc).__name__
                    log.warning("category model unavailable: %s", self._cat_reason)
                self._cat_tried = True
        return self._cat

    @property
    def category_model_status(self) -> dict[str, Any]:
        return {
            "status": "loaded" if self._cat is not None
            else "unavailable" if self._cat_tried else "not_loaded_yet",
            "reason": self._cat_reason,
            "version": getattr(self._cat, "version", None),
        }  # fmt: skip

    async def text_scorer_runtime(self) -> dict[str, Any]:
        """Runtime state of the call-guard script classifier (loads the scorer if needed)."""
        scorer = await self._get_scorer()
        if scorer is None:
            return {"status": "unavailable", "reason": self._failed or "not_loadable"}
        if getattr(scorer, "fallback_mode", False):
            return {"status": "fallback_rules_only", "reason": "classifier_artifact_not_loaded"}
        return {"status": "loaded", "reason": None}

    async def category_runtime(self) -> dict[str, Any]:
        await self._get_category_model()
        st = self.category_model_status
        if st["status"] == "loaded":
            return {"status": "loaded", "reason": None}
        return {"status": "unavailable", "reason": st["reason"] or "unavailable"}

    async def category_health(self) -> dict[str, Any]:
        await self._get_category_model()
        st = self.category_model_status
        return {
            "status": "loaded" if st["status"] == "loaded" else "unavailable",
            "pin_ok": st["status"] == "loaded",
            "reason": st["reason"], "version": st["version"],
        }  # fmt: skip

    async def analyse(self, complaint: dict[str, Any]) -> AnalysisOutcome:  # noqa: C901
        from . import provenance as pv
        from .explain import explanation_parts
        from .signals import analyse_url, check_entities, context_fields

        layout = pv.TextLayout(
            complaint.get("title"), complaint.get("description"), MAX_TEXT_CHARS,
            complaint.get("_text_field") or "description",
        )  # fmt: skip
        text = layout.text
        # fewer than MIN_WORDS words: the text models are not asked and no level is given
        insufficient = len(_WORD.findall(text)) < MIN_WORDS
        selected = complaint.get("category")
        caps: dict[str, str] = dict(CAPABILITIES_NONE)
        reasons: dict[str, str] = {}
        indicators: list[dict[str, Any]] = []
        limitations: list[str] = []
        versions: dict[str, str | None] = {
            "call_guard_rules": None, "call_guard_classifier": None, "category": None, "llm": None,
        }  # fmt: skip
        stages: list[dict[str, Any]] = []

        def stage(name: str, source: str, method: str, status: str, reason: str | None = None,
                  version: str | None = None, **extra: Any) -> None:  # fmt: skip
            stages.append({"stage": name, "source": source, "method": method, "status": status,
                           "reason": reason, "version": version, **extra})  # fmt: skip

        # (a) text scam scoring (call-guard rules + clf-v1 script classifier)
        unexplained: dict[str, int] = {}
        text_score: int | None = None
        text_version = "text-scorer-unavailable"
        scorer = None if insufficient else await self._get_scorer()
        if insufficient:
            caps["text_risk_scorer"] = SKIPPED_SHORT
            reasons["text_risk_scorer"] = "insufficient_input"
        elif scorer is None:
            reasons["text_risk_scorer"] = self._failed or "not_installed_or_not_loadable"
        else:
            try:
                score, rs, text_version = await asyncio.to_thread(_explained_score, scorer, text)
                text_score = max(0, min(100, round(float(score) * 100)))
                if text_score and not _has_reason(rs):
                    # points without any listed reason do not count (defence in depth)
                    unexplained["text_score"], text_score = text_score, 0
                indicators += pv.tag_call_guard(list(rs), layout, text_version, _humanise)
                caps["text_risk_scorer"] = "available"
            except Exception as exc:
                log.warning("text scoring failed: %s", type(exc).__name__)
                reasons["text_risk_scorer"] = "scoring_error"
        rules_version = _rules_version()
        if insufficient:
            stage("text_rules", "rule", pv.RULES_METHOD, "skipped", "insufficient_input")
            stage("script_classifier", "ml", pv.classifier_method(None), "skipped",
                  "insufficient_input")  # fmt: skip
        elif text_score is None:
            why = reasons["text_risk_scorer"]
            stage("text_rules", "rule", pv.RULES_METHOD, "unavailable", why)
            stage("script_classifier", "ml", pv.classifier_method(None), "unavailable", why)
        else:
            versions["call_guard_rules"] = rules_version
            stage("text_rules", "rule", pv.RULES_METHOD, "ran", version=rules_version)
            if text_version == rules_version:  # the scorer answered rules-only
                why = "fallback_rules_only" if getattr(scorer, "fallback_mode", False) else (
                    "classifier_inference_error")  # fmt: skip
                stage("script_classifier", "ml", pv.classifier_method(None), "unavailable", why)
            else:
                versions["call_guard_classifier"] = text_version
                stage("script_classifier", "ml", pv.classifier_method(text_version), "ran",
                      version=text_version)  # fmt: skip

        # (a2) audio evidence transcripts (made by the API before this call; see
        # complaints.transcribe): scored by the same call-guard scorer (rules + clf-v1) as the
        # description; indicators point to the evidence id and the segment's time range
        from .transcribe import TRANSCRIBED

        audio_ev = [e for e in complaint.get("evidence") or []
                    if str(e.get("mime_type") or "").startswith("audio/")]  # fmt: skip
        tr_files = [
            (str(e.get("evidence_id") or ""), list((e.get("transcription") or {}).get("segments")
                                                   or []))
            for e in audio_ev if (e.get("transcription") or {}).get("status") in TRANSCRIBED
        ]  # fmt: skip
        tlayout = pv.TranscriptLayout(tr_files, MAX_TEXT_CHARS) if tr_files else None
        transcript_score: int | None = None
        transcript_scoring: str | None = None  # ran | no_speech | skipped | unavailable
        if tlayout is not None:
            if not tlayout.text.strip():
                transcript_scoring = "no_speech"
            elif insufficient:
                transcript_scoring = "skipped"
            elif scorer is None:
                transcript_scoring = "unavailable"
                reasons["audio_transcript_scoring"] = reasons.get(
                    "text_risk_scorer", "not_installed_or_not_loadable")
            else:
                try:
                    t_score, t_rs, t_ver = await asyncio.to_thread(
                        _explained_score, scorer, tlayout.text
                    )
                    transcript_score = max(0, min(100, round(float(t_score) * 100)))
                    if transcript_score and not _has_reason(t_rs):
                        unexplained["transcript_score"], transcript_score = transcript_score, 0
                    indicators += pv.tag_transcript(list(t_rs), tlayout, t_ver, _humanise)
                    transcript_scoring = "ran"
                except Exception as exc:
                    log.warning("transcript scoring failed: %s", type(exc).__name__)
                    transcript_scoring = "unavailable"
                    reasons["audio_transcript_scoring"] = "scoring_error"
        caps["audio_transcript_scoring"] = {
            None: "skipped_no_input", "ran": "available", "no_speech": "skipped_no_input",
            "skipped": SKIPPED_SHORT, "unavailable": "unavailable",
        }[transcript_scoring]  # fmt: skip
        if transcript_scoring == "no_speech":
            reasons["audio_transcript_scoring"] = "no_speech_in_transcripts"
        if tlayout is not None:
            stage("transcript_scoring", "rule", pv.RULES_METHOD,
                  {"ran": "ran", "unavailable": "unavailable"}.get(transcript_scoring or "",
                                                                   "skipped"),
                  None if transcript_scoring == "ran" else (
                      reasons.get("audio_transcript_scoring") or transcript_scoring),
                  version=rules_version if transcript_scoring == "ran" else None)  # fmt: skip

        # (b) category classification with abstention
        classification: str | None = None
        confidence: float | None = None
        cat_detail: dict[str, Any] | None = None
        cat_model = None if insufficient else await self._get_category_model()
        cat_method = str(getattr(cat_model, "version", None) or pv.CATEGORY_METHOD)
        if insufficient:
            caps["category_classifier"] = SKIPPED_SHORT
            reasons["category_classifier"] = "insufficient_input"
        elif cat_model is None:
            reasons["category_classifier"] = self._cat_reason or "unavailable"
        else:
            # transcripts of audio evidence are part of what the complaint says: classify the
            # description together with them (bounded like the description)
            cat_text = text
            n_transcripts = 0
            if tlayout is not None and tlayout.text.strip():
                cat_text = f"{text}\n{tlayout.text}"[: 2 * MAX_TEXT_CHARS]
                n_transcripts = len(tr_files)
            try:
                pred = await asyncio.to_thread(cat_model.predict, cat_text)
                classification, confidence = pred.label, pred.confidence
                caps["category_classifier"] = "available"
                top3 = [{"category": c, "score": p} for c, p in pred.top[:3]]
                cat_detail = {
                    "label": pred.label, "abstained": pred.abstained,
                    "top": top3, "top_categories": top3,
                    "model_version": cat_model.version,
                    "min_confidence": cat_model.min_confidence,
                    "abstention_threshold": cat_model.min_confidence,
                    "trained_on": "synthetic data only",
                    "source": "ml", "method": cat_method,
                    "evidence": {"type": "whole_text", "field": layout.field_label,
                                 "audio_transcripts_included": n_transcripts},
                }  # fmt: skip
                versions["category"] = cat_model.version
            except Exception as exc:
                log.warning("category classification failed: %s", type(exc).__name__)
                reasons["category_classifier"] = "inference_error"
        if insufficient:
            stage("category_classifier", "ml", cat_method, "skipped", "insufficient_input")
        elif cat_detail is not None:
            stage("category_classifier", "ml", cat_method, "ran", version=versions["category"])
        else:
            stage("category_classifier", "ml", cat_method, "unavailable",
                  reasons["category_classifier"])  # fmt: skip

        # (c) URL heuristics (offline)
        url = complaint.get("suspicious_url")
        url_field = complaint.get("_url_field") or "suspicious_url"
        url_analysis: dict[str, Any] | None = None
        if url:
            url_analysis = analyse_url(str(url))
            ok = url_analysis["status"] == "analysed"
            caps["url_heuristics"] = "available" if ok else "unavailable"
            if not ok:
                reasons["url_heuristics"] = "url_unparseable"
            url_analysis["indicators"] = [
                pv.tag_url(i, url_field, url_analysis.get("host"))
                for i in url_analysis["indicators"]
            ]
            indicators += url_analysis["indicators"]
            stage("url_heuristics", "rule", pv.URL_METHOD, "ran" if ok else "unavailable",
                  None if ok else "url_unparseable")  # fmt: skip
        else:
            caps["url_heuristics"] = "skipped_no_input"
            stage("url_heuristics", "rule", pv.URL_METHOD, "skipped", "no_url")
        caps["url_fetch_reputation"] = "unavailable"
        reasons["url_fetch_reputation"] = "no_network_lookup_by_design"

        # (d) entity and format checks (context, weight 0)
        entities = pv.tag_entities(check_entities(complaint), complaint.get("_field_names"))
        caps["entity_format_checks"] = "available" if entities["checks"] else "skipped_no_input"
        stage("entity_checks", "rule", pv.ENTITY_METHOD,
              "ran" if entities["checks"] else "skipped",
              None if entities["checks"] else "no_phone_upi_or_email")  # fmt: skip
        caps["contact_reputation_lookup"] = "unavailable"
        reasons["contact_reputation_lookup"] = "no_reputation_service_configured"

        # (e) geographic context (recorded only)
        ctx = context_fields(complaint)
        has_geo = ctx["geographic"]["present"]
        caps["geographic_context"] = "available" if has_geo else "skipped_no_input"

        # (f) attachments: audio is transcribed (per file, see above); OCR and document text are
        # not available; list what was not analysed with the precise reason
        unanalysed, kinds = _attachments(complaint.get("evidence"))
        audio_results = [_audio_summary(e) for e in audio_ev]
        failed_audio = sorted({a["status"] for a in audio_results
                               if a["status"] not in TRANSCRIBED})  # fmt: skip
        if not audio_ev:
            caps["audio_transcription"] = "skipped_no_input"
            reasons["audio_transcription"] = "no_audio_evidence"
        elif not failed_audio:
            caps["audio_transcription"] = "available"
        else:
            caps["audio_transcription"] = "unavailable"
            reasons["audio_transcription"] = (
                failed_audio[0] if len(failed_audio) == 1 else "multiple_reasons"
            )
        audio_missing = bool(failed_audio) or transcript_scoring == "unavailable"
        caps["screenshot_ocr"] = "unavailable"
        reasons["screenshot_ocr"] = "no_ocr_engine_installed"
        caps["document_text_extraction"] = "unavailable"
        reasons["document_text_extraction"] = "not_implemented"
        reasons["evidence_content_analysis"] = "audio_only_via_transcription"

        # (h) risk score
        weights = [float(i["weight"]) for i in indicators if i.get("stage") == "url_heuristics"]
        miss = 1.0
        for w in weights:
            miss *= 1.0 - max(0.0, min(1.0, w))
        adj = round(URL_ADJUSTMENT_MAX * (1.0 - miss))
        # the predicted category is shown separately as a model guess; it never adds to the score
        # (accuracy audit 2026-10-17: a category guess alone produced HIGH for legitimate text)
        bases = [b for b in (text_score, transcript_score) if b is not None]
        risk: int | None = None
        if bases or (url_analysis and weights):
            risk = max(0, min(100, (max(bases) if bases else 0) + adj))
        scored = (
            text_score is not None or classification is not None
            or bool(weights) or transcript_score is not None
        )  # fmt: skip
        level: str | None = band_v2(risk) if risk is not None else None
        rule_backed = [i for i in indicators if i.get("source") == "rule"]
        capped_model_only = False
        if level in ("HIGH", "CRITICAL") and not rule_backed:
            # a model score alone is a prediction, not evidence enough for HIGH/CRITICAL
            risk, level, capped_model_only = min(risk or 0, 69), "MEDIUM", True
        withheld = False
        withheld_audio = False
        if insufficient:
            risk, level = None, None
        elif (text_score is None or cat_detail is None) and level in (None, "LOW"):
            # never LOW unless both core text stages (scorer and category model) ran
            risk, level, withheld = None, None, True
        elif audio_missing and level in (None, "LOW"):
            # never LOW while an uploaded recording could not be transcribed and scored
            risk, level, withheld, withheld_audio = None, None, True, True

        # status: applicable stages that were not available are named
        applicable_failed: list[str] = []
        # optional stages (the LLM wording) are reported but never make the analysis PARTIAL:
        # the score, level, category and rule explanation do not depend on them
        optional_failed: list[str] = []
        for cap in ("text_risk_scorer", "category_classifier"):
            if caps[cap] in UNAVAILABLE_STATES:
                applicable_failed.append(cap)
        if url and caps["url_heuristics"] in UNAVAILABLE_STATES:
            applicable_failed.append("url_heuristics")
        if failed_audio:
            applicable_failed.append("audio_transcription")
        if transcript_scoring == "unavailable":
            applicable_failed.append("audio_transcript_scoring")
        if "screenshot_or_image" in kinds:
            applicable_failed.append("screenshot_ocr")
        if "document" in kinds:
            applicable_failed.append("document_text_extraction")

        # (g) explanations. Rule text first; the LLM text is additional and never feeds back.
        absent = [
            n for f, n in _FIELD_NAMES.items()
            if complaint.get(f) in (None, "") and f != "suspect_email"
        ]  # fmt: skip
        facts = {
            "risk_score": risk, "risk_level": level, "indicators": indicators,
            "classification": classification, "selected_category": selected,
            "text_scorer_ran": text_score is not None, "url_analysed": bool(url_analysis),
            "absent_fields": absent,
            "unavailable": [c.replace("_", " ") for c in applicable_failed],
            "category_model_version": versions["category"],
            "insufficient_input": insufficient,
            "transcripts_scored": transcript_scoring == "ran",
            "transcripts_no_speech": transcript_scoring == "no_speech",
        }  # fmt: skip
        parts = explanation_parts(facts)
        explanation = " ".join(p["text"] for p in parts)
        llm_text: str | None = None
        caps["llm_explanation"] = "unavailable"
        reasons["llm_explanation"] = "not_configured"
        llm_configured = bool(self._explainer is not None and self._explainer.configured)
        llm_method = f"ollama:{getattr(self._explainer, 'model', None) or 'not_configured'}"
        if llm_configured:
            if not scored or insufficient:
                caps["llm_explanation"] = "skipped_no_input"
                reasons["llm_explanation"] = "nothing_to_explain"
            elif not indicators:
                # the LLM may only rephrase findings; with none there is nothing to rephrase
                caps["llm_explanation"] = "skipped_no_input"
                reasons["llm_explanation"] = "no_findings_to_rephrase"
            else:
                guidance_pre = (
                    classification
                    if classification and classification not in ("UNDETERMINED", "OTHER")
                    else selected
                )
                pre_actions = static_guidance(guidance_pre, level)
                res = await self._explainer.explain(
                    text, {**facts, "indicators": indicators, "actions": pre_actions}
                )
                caps["llm_explanation"] = res.status
                if res.status == "available":
                    llm_text = res.text
                    reasons.pop("llm_explanation", None)
                else:
                    reasons["llm_explanation"] = res.reason or res.status
                    optional_failed.append("llm_explanation")
        if llm_text:
            versions["llm"] = self._explainer.model if self._explainer else None
            stage("llm_explanation", "llm", llm_method, "ran", version=versions["llm"])
            parts.append({
                "text": llm_text, "source": "llm", "method": llm_method,
                "evidence": {"type": "generated", "inputs": "the computed indicators, their "
                             "sanitised evidence snippets and the recommended steps only",
                             "changes_score": False, "grounding_checked": True},
                "label": f"LLM-generated ({versions['llm']}), may be imprecise",
            })  # fmt: skip
        else:
            stage("llm_explanation", "llm", llm_method,
                  "not_configured" if not llm_configured else
                  "skipped" if caps["llm_explanation"] == "skipped_no_input" else
                  caps["llm_explanation"] if caps["llm_explanation"] in ("rejected", "timeout",
                                                                         "error") else
                  "unavailable",
                  reasons.get("llm_explanation"))  # fmt: skip

        status = "UNAVAILABLE" if not scored else ("PARTIAL" if applicable_failed else "COMPLETED")
        if insufficient:
            status = "INSUFFICIENT_INPUT"
        if status in ("UNAVAILABLE", "INSUFFICIENT_INPUT"):
            risk, level = None, None

        # what the result can and cannot say (shown instead of a bare level)
        evidence_ind = [i for i in indicators if i.get("source") in ("rule", "ml")]
        if status == "INSUFFICIENT_INPUT":
            assessment = "insufficient_evidence"
        elif status == "UNAVAILABLE" or level is None:
            assessment = "not_assessed"
        elif not evidence_ind:
            assessment = "no_indicators_found"
        elif not rule_backed:
            assessment = "model_prediction_only"
        else:
            assessment = "indicators_found"

        # limitations (exact, no overclaims)
        if assessment == "no_indicators_found":
            limitations.append(
                "No scam indicators were found by the rules and models that ran. This is not a "
                "finding that the message or event is safe: these checks cover a limited set of "
                "scam patterns."
            )
        if assessment == "model_prediction_only" or capped_model_only:
            limitations.append(
                "The score comes from a machine-learning model only, with no rule-based indicator; "
                "it is a prediction, not evidence, and is capped at MEDIUM."
            )
        if classification and classification not in ("UNDETERMINED", "OTHER"):
            limitations.append(
                "The predicted category is a model guess (trained on synthetic data); it is not "
                "evidence and is not used in the risk score."
            )
        limitations += [
            "The risk score is an uncalibrated heuristic ordering, not a likelihood. Formula: "
            + RISK_FORMULA
            + ".",
            "The text scorer was built for digital-arrest call scripts and trained on simulator "
            "and authored data; it has not been validated on real complaints.",
            "The category model was trained on synthetic template data only and has not been "
            "validated on real complaints; its confidence is an uncalibrated model score.",
            "URL checks are lexical and offline: the page was not opened and no reputation "
            "service was consulted.",
            "Missing optional details are not treated as evidence, and format checks on phone, "
            "UPI id and email carry no risk weight.",
            "Location, amount and payment method are context only and do not change the score.",
            "Recommended actions are general static guidance, not a finding about this complaint.",
        ]
        if withheld and not withheld_audio:
            limitations.append(
                "No risk level is given because a core text stage (the text scorer or the "
                "category model) did not run and the other evidence did not reach MEDIUM; this "
                "is not a finding that the report is low risk."
            )
        if withheld_audio:
            limitations.append(
                "No risk level is given because an uploaded audio recording could not be "
                "transcribed and analysed and the other evidence did not reach MEDIUM; this is "
                "not a finding that the report is low risk."
            )
        if tr_files:
            used = sorted({f"{a['engine']}:{a['model']}" for a in audio_results
                           if a["status"] in TRANSCRIBED and a["engine"]})  # fmt: skip
            limitations.append(
                "Audio evidence was transcribed locally (" + (", ".join(used) or "speech-to-text")
                + "). Machine transcription can mishear words, especially Hindi and Hinglish "
                "speech, so a cue can be missed or misread; time offsets are approximate."
            )
            cut = [a for a in audio_results if a["truncated"]]
            if cut:
                limitations.append(
                    f"{len(cut)} recording(s) were transcribed only up to the per-file limit of "
                    f"{cut[0]['transcribed_s']:g} s; the rest was not analysed."
                )
            if tlayout is not None and tlayout.truncated:
                limitations.append(
                    f"Only the first {MAX_TEXT_CHARS} characters of the audio transcripts were "
                    "scored."
                )
        if insufficient:
            limitations.append(
                f"The text has fewer than {MIN_WORDS} words, so the text scorer and the category "
                "model were not run and no risk level is given; this is not a finding that the "
                "message is safe."
            )
        for cap in applicable_failed:
            limitations.append(
                f"{cap.replace('_', ' ').capitalize()} was not available "
                f"({reasons.get(cap, caps.get(cap, 'unavailable'))})."
            )
        for cap in optional_failed:
            limitations.append(
                f"{cap.replace('_', ' ').capitalize()} (optional wording only) was not used "
                f"({reasons.get(cap, caps.get(cap, 'unavailable'))}); the rule explanation is "
                "shown and the score, level and category are unaffected."
            )
        if unanalysed:
            limitations.append(
                "Some uploaded evidence files were not analysed; they are listed as unanalysed "
                "attachments with the reason."
            )

        guidance_cat = (
            classification if classification and classification not in ("UNDETERMINED", "OTHER")
            else selected
        )  # fmt: skip
        actions = pv.tag_guidance(static_guidance(guidance_cat, level), guidance_cat, level)
        stage("static_guidance", "static_guidance", pv.GUIDANCE_METHOD, "ran")
        for a in audio_results:  # one stage per audio file
            stage("audio_transcription", "ml",
                  f"{a['engine']}:{a['model']}" if a["engine"] and a["model"] else
                  "speech_to_text", _STAGE_STATUS.get(a["status"], "unavailable"),
                  None if a["status"] == "transcribed" else a["status"], version=a["model"],
                  evidence_id=a["evidence_id"], reused=a["reused"])  # fmt: skip
        for cap in ("screenshot_ocr", "document_text_extraction"):
            if cap in applicable_failed:
                stage(cap, "rule" if cap == "document_text_extraction" else "ml", cap,
                      "unavailable", reasons.get(cap))  # fmt: skip
        provenance = {
            "schema": pv.PROVENANCE_SCHEMA, "derived_from_legacy": False, "legacy": False,
            "pipeline": PIPELINE_V2, "stages": stages,
            "ran": [x["stage"] for x in stages if x["status"] == "ran"],
            "unavailable": [{"stage": x["stage"], "reason": x["reason"],
                             **({"evidence_id": x["evidence_id"]} if "evidence_id" in x else {})}
                            for x in stages if x["status"] in _NOT_RUN],
            "skipped": [{"stage": x["stage"], "reason": x["reason"],
                         **({"evidence_id": x["evidence_id"]} if "evidence_id" in x else {})}
                        for x in stages if x["status"] == "skipped"],
            "model_versions": versions,
            "sources": list(pv.SOURCES),
        }  # fmt: skip
        detail = {
            "analysis_schema": 2,
            "assessment": assessment,
            "risk_level": level,
            "explanation_source": "rules",
            "explanation_parts": parts,
            "llm_explanation": llm_text,
            "llm_model": self._explainer.model if llm_text and self._explainer else None,
            "capability_reasons": reasons,
            "unavailable_capabilities": sorted(set(applicable_failed) | set(optional_failed)),
            "unanalysed_attachments": unanalysed,
            "audio_transcription": audio_results,
            "category_model": cat_detail,
            "confidence_semantics": "model_confidence_uncalibrated" if confidence is not None
            else None,  # fmt: skip
            "confidence_interpretation": confidence_interpretation(
                confidence, cat_detail, reasons.get("category_classifier")
            ),
            "url_analysis": url_analysis,
            "entity_checks": entities,
            "context": ctx,
            "score_components": {
                "text_score": text_score, "transcript_score": transcript_score,
                "category_prior": None, "category_used_in_score": False,
                "unexplained_scores_ignored": unexplained,
                "capped_model_only": capped_model_only, "url_adjustment": adj,
                "formula": RISK_FORMULA, "bands": BANDS_V2, "withheld": withheld,
                "source": "rule", "method": "risk_formula",
            },
            "provenance": provenance,
        }  # fmt: skip
        return AnalysisOutcome(
            status=status,
            pipeline_version=(
                f"{PIPELINE_V2}+{text_version}+"
                f"{getattr(cat_model, 'version', 'category-unavailable')}"
            ),
            capabilities=caps,
            risk_score=risk,
            risk_level=level,
            indicators=indicators,
            recommended_actions=actions,
            limitations=limitations,
            classification=classification,
            confidence=confidence,
            explanation=explanation,
            extra=detail,
        )


def _rules_version() -> str:
    try:
        from call_guard.rules import RULES_VERSION

        return str(RULES_VERSION)
    except Exception:
        return "rules-v1"


def confidence_interpretation(
    confidence: float | None, cat: dict[str, Any] | None, reason: str | None = None
) -> str:
    """Plain-language meaning of ``confidence`` (the category model's number)."""
    if confidence is None or cat is None:
        return "No confidence is given: the category model did not run" + (
            f" ({reason.replace('_', ' ')})." if reason else "."
        )
    thr = cat.get("abstention_threshold", cat.get("min_confidence"))
    ver = cat.get("model_version") or "the category model"
    base = (
        f"{confidence:.2f} is the top-class score (0-1) of {ver}, a model trained on 100% "
        "synthetic data. It is uncalibrated: it is not the chance that the category is correct "
        "and has not been validated on real complaints."
    )
    if cat.get("abstained"):
        return (
            f"The category is UNDETERMINED because the model's top-class score "
            f"({confidence:.2f}) was below the abstention threshold ({thr}). " + base
        )
    return base + f" Below the abstention threshold ({thr}) the category would be UNDETERMINED."

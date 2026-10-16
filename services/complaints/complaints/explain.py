"""Explanations: a deterministic rule-based text always, plus an optional local-LLM text.

* ``rules_explanation`` builds the explanation from the indicators that were actually computed.
  It is always produced (``explanation_source: rules``) and uses fixed sentence templates.
* ``OllamaClient`` talks to a LOCAL Ollama server (``OLLAMA_URL``, loopback / private / single-
  label hosts only) through ``/api/generate`` (non-streaming, temperature 0.1, at most 120 tokens,
  hard timeout, default 8 s). The prompt is GROUNDED: it lists only the computed indicators (code,
  plain text and a sanitised evidence snippet), the level, the suggested category and the static
  recommended steps, and asks for a rephrasing of exactly those. The complaint text itself is not
  sent. Small models invent rationale (a smoke test of qwen2.5:0.5b claimed a fake-CBI "safe
  account" demand meant "the caller has evidence of a financial transaction"), so the output is
  checked by the shared ``svckit.llm_grounding`` module: links, addresses and numbers it invents
  are stripped; it is REJECTED when empty, too long, not in the requested language, claiming a
  likelihood or a certainty / evidence, turning the accusation onto the caller (role inversion,
  checked per sentence ignoring quotes and case), naming a scam cue that is not among the given
  indicators, or not referring to any given indicator. A
  rejection, timeout or error yields no LLM text and a named status (``rejected`` / ``timeout`` /
  ``error`` / ``unavailable``); the rule-based explanation stays primary and the LLM never
  touches the score, level or classification.
* ``probe`` is a cheap ``GET /api/tags`` used by the admin health view (cached 30 s). It never
  loads or pulls a model.

Unit tests use a fake HTTP transport; ``tests/test_ollama_live.py`` runs against a live local
Ollama only with ``OLLAMA_LIVE_TESTS=1``.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from svckit import llm_grounding as g

log = logging.getLogger("complaints.explain")

MAX_PROMPT_TEXT = 1500  # characters of sanitised description sent to the model
MAX_LLM_OUTPUT = 700  # characters; longer output is rejected, not truncated
NUM_PREDICT = 120  # tokens
MAX_FINDINGS = 5  # strongest indicators first; more only invites repetition from a small model
MAX_SENTENCES = 3
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CONCURRENT = 2
PROBE_TTL_SECONDS = 30.0
# qwen2.5:0.5b fits an 8 GB machine; qwen2.5:3b is preferred when the hardware allows (OLLAMA_MODEL)
DEFAULT_MODEL = "qwen2.5:0.5b"
DEFAULT_TIMEOUT_S = 8.0

_URL = g.URL
_EMAIL_UPI = g.EMAIL_UPI
_PHONE = g.PHONE
_DIGITS = g.DIGITS
_CLAIM = g.CLAIM
CUE_WORDS = g.CUE_WORDS
tidy = g.tidy
strip_invented = g.strip_invented


def sanitise(text: str, limit: int = MAX_PROMPT_TEXT) -> str:
    t = _EMAIL_UPI.sub("[id]", text)
    t = _URL.sub("[link]", t)
    t = _PHONE.sub("[number]", t)
    t = _DIGITS.sub("[number]", t)
    return t[:limit]


def build_prompt(description: str, facts: dict[str, Any], lang: str = "en") -> str:
    """Grounded prompt: only the computed findings and steps, never the complaint text itself
    (``description`` is accepted for API compatibility and not used)."""
    del description
    ind = sorted(facts.get("indicators") or [], key=lambda i: -float(i.get("weight") or 0))
    ind = ind[:MAX_FINDINGS]
    lines = [
        "Rewrite the findings below as two or three short, plain English sentences for the person "
        "who reported this message.",
        "Rules: use ONLY the findings and steps listed. Do not add any new fact, reason, guess, "
        "name, number or link. Do not say the message is safe, confirmed or proven. Do not state "
        "a probability, percentage or chance. Write in English only.",
        "",
        f"Category suggested by a model: {facts.get('classification') or 'not determined'}",
        "Findings:",
    ]
    for n, i in enumerate(ind, 1):
        line = f"{n}. {sanitise(str(i.get('text', '')), 160)}"
        ev = i.get("evidence") or {}
        snip = ev.get("snippet")
        if snip:
            where = "the recording says" if ev.get("type") == "evidence_transcript" else (
                "the message says")  # fmt: skip
            line += f' ({where}: "{sanitise(str(snip), 160)}")'
        lines.append(line)
    if not ind:
        lines.append("- No scam cue was detected by the checks.")
    steps = [a.get("text", "") for a in (facts.get("actions") or [])[:3]]
    if steps:
        lines += ["Recommended steps:", *[f"- {sanitise(str(t), 200)}" for t in steps]]
    lines += ["", "Answer:"]
    return "\n".join(lines)


def validate_output(text: Any, prompt: str) -> str | None:
    """Safety check. Return a rejection reason, or None when the text is acceptable."""
    return g.validate_output(text, prompt, MAX_LLM_OUTPUT)


def findings_of(facts: dict[str, Any]) -> tuple[list[str], str]:
    """The findings given to the model (indicator text, evidence snippet, code words) and the
    context in which a named cue also counts as grounded (the suggested category)."""
    findings = [
        f"{i.get('text', '')} {(i.get('evidence') or {}).get('snippet', '')} "
        f"{str(i.get('code', '')).replace('_', ' ')}"
        for i in facts.get("indicators") or []
    ]
    return findings, str(facts.get("classification") or "").replace("_", " ")


def ground_output(text: str, prompt: str, facts: dict[str, Any], lang: str = "en") -> str | None:
    """Grounding check (``svckit.llm_grounding``). Return a rejection reason, or None when the
    text only rephrases the findings it was given."""
    del prompt
    findings, context = findings_of(facts)
    return g.ground_output(text, findings, context, lang)


@dataclass(frozen=True)
class LlmResult:
    status: str  # available | unavailable | timeout | error | rejected
    text: str | None
    reason: str | None = None


class OllamaClient:
    def __init__(
        self,
        url: str | None,
        model: str = DEFAULT_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = (url or "").rstrip("/") or None
        self.model = model
        self.timeout_s = timeout_s
        self._transport = transport
        self._clock = clock
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        self._probe_cache: tuple[float, tuple[str, list[str]]] | None = None

    @property
    def configured(self) -> bool:
        return self.url is not None

    @property
    def is_local(self) -> bool:
        """Only a LOCAL Ollama is ever contacted: loopback, ``*.localhost``, a private or
        link-local IP literal, or a single-label host name (a container on the same compose
        network, e.g. ``ollama``). Anything else is refused as ``non_local_host``."""
        return g.is_local_url(self.url)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=httpx.Timeout(self.timeout_s), follow_redirects=False
        )

    async def explain(self, description: str, facts: dict[str, Any], lang: str = "en") -> LlmResult:
        if not self.configured:
            return LlmResult("unavailable", None, "not_configured")
        if not self.is_local:
            return LlmResult("unavailable", None, "non_local_host")
        prompt = build_prompt(description, facts, lang)
        body = {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.1, "num_predict": NUM_PREDICT},
        }  # fmt: skip
        try:
            async with self._sem, asyncio.timeout(self.timeout_s):
                async with self._client() as c:
                    r = await c.post(f"{self.url}/api/generate", json=body)
        except (TimeoutError, httpx.TimeoutException):
            return LlmResult("timeout", None, "timeout")
        except httpx.TransportError:
            return LlmResult("unavailable", None, "unreachable")
        except Exception as exc:  # defensive: never let an explainer break the analysis
            log.warning("llm explanation failed: %s", type(exc).__name__)
            return LlmResult("error", None, "unexpected_error")
        if r.status_code >= 400:
            return LlmResult("error", None, f"http_{r.status_code // 100}xx")
        if len(r.content) > MAX_RESPONSE_BYTES:
            return LlmResult("error", None, "response_too_large")
        try:
            data = r.json()
            text = data.get("response") if isinstance(data, dict) else None
        except ValueError:
            return LlmResult("error", None, "bad_response")
        if not isinstance(text, str):
            return LlmResult("error", None, "bad_response")
        findings, context = findings_of(facts)
        review = g.review_output(
            text, prompt, findings, context, lang, MAX_LLM_OUTPUT, MAX_SENTENCES
        )
        if review.text is None:
            log.info("llm explanation rejected: %s", review.reason)  # reason only, never text
            return LlmResult("rejected", None, review.reason)
        return LlmResult("available", review.text)

    async def probe(self) -> str:
        """``not_configured`` | ``reachable`` | ``unreachable``; cached for 30 s."""
        if not self.configured:
            return "not_configured"
        if not self.is_local:
            return "non_local_host"
        tags = await self._tags()
        return "reachable" if tags[0] == "ok" else "unreachable"

    async def _tags(self) -> tuple[str, list[str]]:
        """``GET /api/tags`` (cached 30 s): (``ok`` | ``unreachable`` | ``bad_response``, names).
        Never called for a non-local host; never pulls or loads a model."""
        now = self._clock()
        if self._probe_cache and now - self._probe_cache[0] < PROBE_TTL_SECONDS:
            return self._probe_cache[1]
        out: tuple[str, list[str]] = ("unreachable", [])
        try:
            async with self._client() as c:
                r = await asyncio.wait_for(c.get(f"{self.url}/api/tags"), min(self.timeout_s, 5.0))
            if r.status_code == 200:
                try:
                    data = r.json()
                    models = data.get("models") if isinstance(data, dict) else None
                    names = [str(m.get("name")) for m in models or [] if isinstance(m, dict)]
                    out = ("ok", names) if isinstance(models, list) else ("bad_response", [])
                except ValueError:
                    out = ("bad_response", [])
        except Exception:  # connection refused, timeout, DNS...
            out = ("unreachable", [])
        self._probe_cache = (now, out)
        return out

    async def status(self) -> dict[str, Any]:
        """``not_configured`` | ``unavailable`` (with a reason) | ``ready`` (the model is pulled
        on the local server)."""
        if not self.configured:
            return {"status": "not_configured", "model": None, "reason": None}
        if not self.is_local:
            return {"status": "unavailable", "model": self.model, "reason": "non_local_host"}
        state, names = await self._tags()
        if state != "ok":
            return {"status": "unavailable", "model": self.model, "reason": state}
        pulled = self.model in names or f"{self.model}:latest" in names
        return {"status": "ready" if pulled else "unavailable", "model": self.model,
                "reason": None if pulled else "model_not_pulled"}  # fmt: skip


# ----------------------------------------------------------------------------- rules text
def _part(text: str, source: str, method: str, **evidence: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"text": text, "source": source, "method": method}
    if evidence:
        out["evidence"] = evidence
    return out


def explanation_parts(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic explanation built from the computed facts only, one part per sentence, each
    with the provenance of the fact it states (see ``complaints.provenance``)."""
    parts: list[dict[str, Any]] = []
    score, level = facts.get("risk_score"), facts.get("risk_level")
    inds = facts.get("indicators", [])
    if facts.get("insufficient_input"):
        parts.append(_part(
            "The text is too short to analyse, so no risk score or category was given. This is "
            "not a finding that the message is safe.", "rule", "input_check",
            type="input", field="text",
        ))  # fmt: skip
    elif score is None or level is None:
        parts.append(_part(
            "No risk score could be given: the text risk scorer was not available or the "
            "evidence available was not enough to say the report is low risk.",
            "rule", "risk_formula", type="score_components",
        ))  # fmt: skip
    else:
        parts.append(_part(
            f"Risk score {score}/100 ({level}). This is an uncalibrated heuristic ordering, not "
            "a likelihood or a verdict.", "rule", "risk_formula", type="score_components",
        ))  # fmt: skip
    rule_ind = [i for i in inds if i.get("stage") == "call_guard" and i.get("source") == "rule"]
    ml_ind = [i for i in inds if i.get("stage") == "call_guard" and i.get("source") == "ml"]
    if rule_ind:
        parts.append(_part(
            "The text matched: " + "; ".join(str(i["text"]) for i in rule_ind[:5]) + ".",
            "rule", "call_guard.rules", type="indicators", codes=[i["code"] for i in rule_ind[:5]],
        ))  # fmt: skip
    elif facts.get("text_scorer_ran"):
        parts.append(_part(
            "The rules matched no specific scam cue in the text." if ml_ind
            else "The text matched no specific scam cue.", "rule", "call_guard.rules",
            type="indicators", codes=[],
        ))  # fmt: skip
    for i in ml_ind[:1]:
        parts.append(_part(
            "The script classifier found the text similar to known digital-arrest call scripts.",
            "ml", str(i.get("method") or "call_guard.clf-v1"), type="indicators",
            codes=[i["code"]],
        ))  # fmt: skip
    cls = facts.get("classification")
    cat_method = str(facts.get("category_model_version") or "category-v1")
    if cls == "UNDETERMINED":
        parts.append(_part(
            "The category model could not decide on a category, so none is assigned.",
            "ml", cat_method, type="model_output",
        ))  # fmt: skip
    elif cls:
        parts.append(_part(
            f"The category model suggests {cls.replace('_', ' ').lower()}.", "ml", cat_method,
            type="model_output",
        ))  # fmt: skip
    sel = facts.get("selected_category")
    if cls and cls not in ("UNDETERMINED", sel) and sel:
        parts.append(_part(
            f"The complainant selected {sel.replace('_', ' ').lower()}; this was not used as "
            "evidence.", "rule", "explanation_template", type="field", field="category",
        ))  # fmt: skip
    url_ind = [i for i in inds if i.get("stage") == "url_heuristics"]
    if url_ind:
        parts.append(_part(
            "The link has these features: " + "; ".join(str(i["text"]) for i in url_ind[:5]) + ".",
            "rule", "url_heuristics", type="field", field="suspicious_url",
        ))  # fmt: skip
    elif facts.get("url_analysed"):
        parts.append(_part(
            "The link string showed none of the checked suspicious features.", "rule",
            "url_heuristics", type="field", field="suspicious_url",
        ))  # fmt: skip
    if facts.get("url_analysed"):
        parts.append(_part("The page itself was not opened or looked up.", "rule",
                           "url_heuristics", type="field", field="suspicious_url"))  # fmt: skip
    absent = facts.get("absent_fields") or []
    if absent:
        parts.append(_part(
            "Details not supplied (" + ", ".join(absent) + ") were not treated as evidence.",
            "rule", "explanation_template", type="absent_fields",
        ))  # fmt: skip
    unavailable = facts.get("unavailable") or []
    if unavailable:
        parts.append(_part("Not analysed: " + ", ".join(unavailable) + ".", "rule",
                           "pipeline_status", type="capabilities"))  # fmt: skip
    return parts


def rules_explanation(facts: dict[str, Any]) -> str:
    """The rule-based explanation as one text (the parts joined)."""
    return " ".join(p["text"] for p in explanation_parts(facts))

"""Explanations: a deterministic rule-based text always, plus an optional local-LLM text.

* ``rules_explanation`` builds the explanation from the indicators that were actually computed.
  It is always produced (``explanation_source: rules``) and uses fixed sentence templates.
* ``OllamaClient`` talks to a LOCAL Ollama server (``OLLAMA_URL``) through ``/api/generate``
  (non-streaming, temperature 0.2, hard timeout). Its prompt contains only the already computed
  indicators and the sanitised description (phone numbers, URLs, UPI ids, emails and long digit
  runs replaced by placeholders). Its output is length-capped and rejected if it contains a link,
  address or number-like token, or claims a likelihood. A failure yields no LLM text and a named
  capability status; the LLM never touches the score, level or classification.
* ``probe`` is a cheap ``GET /api/tags`` used by the admin health view (cached 30 s). It never
  loads or pulls a model.

Not exercised against a live Ollama in this repository (it is not installed here); the tests use
a fake HTTP transport.
"""

import asyncio
import ipaddress
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("complaints.explain")

MAX_PROMPT_TEXT = 1500  # characters of sanitised description sent to the model
MAX_LLM_OUTPUT = 1200  # characters; longer output is rejected, not truncated
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CONCURRENT = 2
PROBE_TTL_SECONDS = 30.0
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT_S = 20.0

_URL = re.compile(
    r"https?://\S+|www\.\S+|"
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|in|xyz|top|click|info|co|io|me|ly|app|site|"
    r"online|icu|buzz|cc|link|gov|edu)\b\S*",
    re.IGNORECASE,
)
_EMAIL_UPI = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{2,}")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,}\d")
_DIGITS = re.compile(r"\d{5,}")
_CLAIM = re.compile(r"probab|likelihood|\bchances?\b|\b\d{1,3}\s?%|percent", re.IGNORECASE)


def sanitise(text: str, limit: int = MAX_PROMPT_TEXT) -> str:
    t = _EMAIL_UPI.sub("[id]", text)
    t = _URL.sub("[link]", t)
    t = _PHONE.sub("[number]", t)
    t = _DIGITS.sub("[number]", t)
    return t[:limit]


def build_prompt(description: str, facts: dict[str, Any]) -> str:
    lines = [
        "You are helping explain the result of an automated complaint triage to the person who "
        "filed it. Write at most four short plain sentences using ONLY the facts below.",
        "Do not add any fact, name, number, link or phone number. Do not state a probability, "
        "percentage or chance. Do not change or restate the risk level as anything else. Do not "
        "give legal advice.",
        "",
        f"Risk level: {facts.get('risk_level') or 'not assessed'}",
        f"Category: {facts.get('classification') or 'not determined'}",
        "Indicators:",
    ]
    ind = facts.get("indicators") or []
    lines += [f"- {i.get('code', '')}: {sanitise(str(i.get('text', '')), 160)}" for i in ind[:12]]
    if not ind:
        lines.append("- none matched")
    lines += ["", "Complaint text (sanitised):", sanitise(description)]
    return "\n".join(lines)


def validate_output(text: Any, prompt: str) -> str | None:
    """Return a rejection reason, or None when the text is acceptable."""
    if not isinstance(text, str) or not text.strip():
        return "bad_response"
    if len(text) > MAX_LLM_OUTPUT:
        return "output_too_long"
    for rx in (_URL, _EMAIL_UPI, _PHONE, _DIGITS):
        for m in rx.finditer(text):
            if m.group(0) not in prompt:
                return "unsafe_content"
    if _CLAIM.search(text):
        return "likelihood_claim"
    return None


@dataclass(frozen=True)
class LlmResult:
    status: str  # available | unavailable | timeout | error
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
        if self.url is None:
            return False
        try:
            host = (urlsplit(self.url).hostname or "").casefold()
        except ValueError:
            return False
        if not host:
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_loopback or ip.is_private or ip.is_link_local
        except ValueError:
            return "." not in host

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=httpx.Timeout(self.timeout_s), follow_redirects=False
        )

    async def explain(self, description: str, facts: dict[str, Any]) -> LlmResult:
        if not self.configured:
            return LlmResult("unavailable", None, "not_configured")
        if not self.is_local:
            return LlmResult("unavailable", None, "non_local_host")
        prompt = build_prompt(description, facts)
        body = {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2, "num_predict": 256},
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
        why = validate_output(text, prompt)
        if why:
            return LlmResult("error", None, why)
        return LlmResult("available", text.strip())  # type: ignore[union-attr]

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

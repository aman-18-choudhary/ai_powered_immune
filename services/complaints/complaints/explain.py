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
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
    t = _URL.sub("[link]", text)
    t = _EMAIL_UPI.sub("[id]", t)
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
        self._probe_cache: tuple[float, str] | None = None

    @property
    def configured(self) -> bool:
        return self.url is not None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=httpx.Timeout(self.timeout_s), follow_redirects=False
        )

    async def explain(self, description: str, facts: dict[str, Any]) -> LlmResult:
        if not self.configured:
            return LlmResult("unavailable", None, "not_configured")
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
        now = self._clock()
        if self._probe_cache and now - self._probe_cache[0] < PROBE_TTL_SECONDS:
            return self._probe_cache[1]
        status = "unreachable"
        try:
            async with self._client() as c:
                r = await asyncio.wait_for(c.get(f"{self.url}/api/tags"), min(self.timeout_s, 5.0))
            if r.status_code == 200:
                status = "reachable"
        except Exception:  # connection refused, timeout, DNS...
            status = "unreachable"
        self._probe_cache = (now, status)
        return status


# ----------------------------------------------------------------------------- rules text
def rules_explanation(facts: dict[str, Any]) -> str:
    """Deterministic explanation built from the computed facts only."""
    parts: list[str] = []
    score, level = facts.get("risk_score"), facts.get("risk_level")
    if score is None or level is None:
        parts.append(
            "No risk score could be given: the text risk scorer was not available or the "
            "evidence available was not enough to say the report is low risk."
        )
    else:
        parts.append(
            f"Risk score {score}/100 ({level}). This is an uncalibrated heuristic ordering, not "
            "a likelihood or a verdict."
        )
    text_ind = [i for i in facts.get("indicators", []) if i.get("source") == "call_guard"]
    if text_ind:
        parts.append("The text matched: " + "; ".join(str(i["text"]) for i in text_ind[:5]) + ".")
    elif facts.get("text_scorer_ran"):
        parts.append("The text matched no specific scam cue.")
    cls = facts.get("classification")
    if cls == "UNDETERMINED":
        parts.append("The category model could not decide on a category, so none is assigned.")
    elif cls:
        parts.append(f"The category model suggests {cls.replace('_', ' ').lower()}.")
    sel = facts.get("selected_category")
    if cls and cls not in ("UNDETERMINED", sel) and sel:
        parts.append(
            f"The complainant selected {sel.replace('_', ' ').lower()}; this was not used as "
            "evidence."
        )
    url_ind = [i for i in facts.get("indicators", []) if i.get("source") == "url_heuristics"]
    if url_ind:
        parts.append(
            "The link has these features: " + "; ".join(str(i["text"]) for i in url_ind[:5]) + "."
        )
    elif facts.get("url_analysed"):
        parts.append("The link string showed none of the checked suspicious features.")
    if facts.get("url_analysed"):
        parts.append("The page itself was not opened or looked up.")
    absent = facts.get("absent_fields") or []
    if absent:
        parts.append(
            "Details not supplied (" + ", ".join(absent) + ") were not treated as evidence."
        )
    unavailable = facts.get("unavailable") or []
    if unavailable:
        parts.append("Not analysed: " + ", ".join(unavailable) + ".")
    return " ".join(parts)

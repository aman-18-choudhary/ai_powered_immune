"""Explanations: a deterministic rules text always; an optional local-LLM text.

* ``rules_explanation`` is built only from the computed level, score and indicator labels.
* ``OllamaExplainer`` follows the approach of ``services/complaints/complaints/explain.py``
  (local Ollama ``/api/generate``, non-streaming, temperature 0.2, hard timeout, output length
  cap, output rejected if it contains a link / id / number not in the prompt or claims a
  likelihood). The prompt carries ONLY indicator codes, labels and REDACTED excerpts (links,
  UPI ids, e-mails, phone numbers and digit runs replaced by placeholders), never the transcript.
  The LLM never changes the score or level. Not exercised against a live Ollama here (not
  installed); tests use a fake HTTP transport.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("live_audio.explain")

DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT_S = 20.0
MAX_LLM_OUTPUT = 1200
MAX_RESPONSE_BYTES = 256 * 1024
PROBE_TTL_S = 30.0

_UPI = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{2,}")
_URL = re.compile(
    r"https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|in|xyz|top|click|info|"
    r"co|io|me|ly|app|site|online|icu|buzz|cc|link|gov|edu|live|club|shop|bank)\b\S*",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,}\d")
_DIGITS = re.compile(r"\d{4,}")
_CLAIM = re.compile(r"probab|likelihood|\bchances?\b|\b\d{1,3}\s?%|percent", re.IGNORECASE)


def redact(text: str, limit: int = 160) -> str:
    t = _UPI.sub("[payment-id]", text)
    t = _URL.sub("[link]", t)
    t = _PHONE.sub("[number]", t)
    t = _DIGITS.sub("[number]", t)
    return t[:limit]


def build_prompt(facts: dict[str, Any]) -> str:
    lines = [
        "You explain the result of an automated check of a phone conversation to the person who "
        "is on the call. Write at most four short plain sentences using ONLY the facts below, "
        "then one sentence of safety advice (hang up, do not pay or share codes, call the bank "
        "on the number printed on the card, report to 1930).",
        "Do not add any fact, name, number or link. Do not state a probability, percentage or "
        "chance. These are indicators detected in speech; this is not a confirmation of fraud.",
        "",
        f"Risk level: {facts.get('level') or 'not assessed'}",
        "Indicators:",
    ]
    ind = facts.get("indicators") or []
    for i in ind[:12]:
        ex = "; ".join(redact(str(x)) for x in (i.get("excerpts") or [])[:2])
        lines.append(
            f"- {i.get('code', '')}: {i.get('label', '')}" + (f' (heard: "{ex}")' if ex else "")
        )
    if not ind:
        lines.append("- none")
    return "\n".join(lines)


def validate_output(text: Any, prompt: str) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "bad_response"
    if len(text) > MAX_LLM_OUTPUT:
        return "output_too_long"
    for rx in (_URL, _UPI, _PHONE, _DIGITS):
        for m in rx.finditer(text):
            if m.group(0) not in prompt:
                return "unsafe_content"
    if _CLAIM.search(text):
        return "likelihood_claim"
    return None


@dataclass(frozen=True)
class LlmResult:
    status: str  # available | not_configured | unavailable | timeout | error
    text: str | None
    reason: str | None = None


class OllamaExplainer:
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
        self._sem: asyncio.Semaphore | None = None
        self._probe: tuple[float, str] | None = None

    @property
    def configured(self) -> bool:
        return self.url is not None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=httpx.Timeout(self.timeout_s), follow_redirects=False
        )

    async def explain(self, facts: dict[str, Any]) -> LlmResult:
        if not self.configured:
            return LlmResult("not_configured", None, "not_configured")
        if self._sem is None:
            self._sem = asyncio.Semaphore(1)
        prompt = build_prompt(facts)
        body = {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2, "num_predict": 200},
        }  # fmt: skip
        try:
            async with self._sem, asyncio.timeout(self.timeout_s):
                async with self._client() as c:
                    r = await c.post(f"{self.url}/api/generate", json=body)
        except (TimeoutError, httpx.TimeoutException):
            return LlmResult("timeout", None, "timeout")
        except httpx.TransportError:
            return LlmResult("unavailable", None, "unreachable")
        except Exception as e:  # never let the explainer break the analysis
            log.warning("llm explanation failed: %s", type(e).__name__)
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
        return LlmResult("available", str(text).strip())

    async def probe(self) -> str:
        """``not_configured`` | ``reachable`` | ``unreachable`` (cached 30 s)."""
        if not self.configured:
            return "not_configured"
        now = self._clock()
        if self._probe and now - self._probe[0] < PROBE_TTL_S:
            return self._probe[1]
        status = "unreachable"
        try:
            async with self._client() as c:
                r = await asyncio.wait_for(c.get(f"{self.url}/api/tags"), min(self.timeout_s, 3.0))
            if r.status_code == 200:
                status = "reachable"
        except Exception:
            status = "unreachable"
        self._probe = (now, status)
        return status


def rules_explanation(facts: dict[str, Any]) -> str:
    level, score = facts.get("level"), facts.get("score")
    ind = facts.get("indicators") or []
    if level is None or score is None:
        return (
            "No risk level is shown: speech could not be transcribed or analysed yet, so the "
            "conversation has not been assessed. This is not a sign that it is safe."
        )
    parts = [f"Risk {level} (score {score}/100, an uncalibrated heuristic, not a probability)."]
    if ind:
        parts.append(
            "Detected in the speech: "
            + "; ".join(str(i.get("label", i.get("code"))) for i in ind[:6])
            + "."
        )
    else:
        parts.append("No scam indicators were detected in the speech so far.")
    parts.append("These are indicators detected in speech, not a confirmation of fraud.")
    return " ".join(parts)

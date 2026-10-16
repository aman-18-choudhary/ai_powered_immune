"""Explanations: a deterministic rules text always; an optional local-LLM text.

* ``rules_explanation`` is built only from the computed level, score and indicator labels. It is
  always shown and stays primary.
* ``OllamaExplainer`` (local Ollama ``/api/generate``, non-streaming, temperature 0.1, at most
  120 tokens, hard timeout) gets a FINDINGS-ONLY prompt: the indicator labels with REDACTED
  excerpts (links, UPI ids, e-mails, phone numbers and digit runs replaced by placeholders) and
  fixed safety steps; never the transcript, the score or the level. The answer is untrusted and
  goes through the shared ``svckit.llm_grounding.review_output`` (the same checks as the
  complaints explainer): invented links / numbers stripped, complete sentences only, rejected
  (``status: rejected`` + reason) when empty, too long, claiming a likelihood or certainty,
  turning the accusation onto the caller (role inversion), naming a cue that was not detected or
  referring to none of the findings. On rejection, timeout or error there is no LLM text and the
  console shows the rules explanation. The LLM never changes the score or level.
* Default model ``qwen2.5:0.5b`` (fits an 8 GB machine; ``qwen2.5:3b`` is preferred when the
  hardware allows, ``OLLAMA_MODEL``). Off unless ``OLLAMA_URL`` is set. Unit tests use a fake
  HTTP transport; ``tests/test_ollama_live.py`` runs against a live local Ollama only with
  ``OLLAMA_LIVE_TESTS=1``.
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
from svckit import llm_grounding as g

log = logging.getLogger("live_audio.explain")

DEFAULT_MODEL = "qwen2.5:0.5b"
DEFAULT_TIMEOUT_S = 20.0
MAX_LLM_OUTPUT = 700
NUM_PREDICT = 120
MAX_FINDINGS = 5
MAX_RESPONSE_BYTES = 256 * 1024
PROBE_TTL_S = 30.0
STEPS = (
    "Hang up the call.",
    "Do not pay, transfer money or share any code.",
    "Call your bank on the number printed on your card and report to the helpline 1930.",
)

_UPI = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{2,}")
_URL = re.compile(
    r"https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|in|xyz|top|click|info|"
    r"co|io|me|ly|app|site|online|icu|buzz|cc|link|gov|edu|live|club|shop|bank)\b\S*",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,}\d")
_DIGITS = re.compile(r"\d{4,}")


def redact(text: str, limit: int = 160) -> str:
    t = _UPI.sub("[payment-id]", text)
    t = _URL.sub("[link]", t)
    t = _PHONE.sub("[number]", t)
    t = _DIGITS.sub("[number]", t)
    return t[:limit]


def _ranked(facts: dict[str, Any]) -> list[dict[str, Any]]:
    return list(facts.get("indicators") or [])[:MAX_FINDINGS]


def build_prompt(facts: dict[str, Any]) -> str:
    """Findings-only prompt: indicator labels with redacted excerpts and fixed steps."""
    lines = [
        "Rewrite the findings below as two or three short, plain English sentences for the person "
        "who is on this phone call.",
        "Rules: use ONLY the findings and steps listed. Do not add any new fact, reason, guess, "
        "name, number or link. Do not say the call is safe, confirmed or proven. Do not state a "
        "probability, percentage or chance. These are indicators heard in the speech, not a "
        "confirmation of fraud. Write in English only.",
        "",
        "Findings:",
    ]
    ind = _ranked(facts)
    for n, i in enumerate(ind, 1):
        ex = "; ".join(redact(str(x)) for x in (i.get("excerpts") or [])[:2])
        heard = f' (heard: "{ex}")' if ex else ""
        lines.append(f"{n}. {redact(str(i.get('label', '')))}{heard}")
    if not ind:
        lines.append("- No scam cue was detected in the speech.")
    lines += ["Recommended steps:", *[f"- {t}" for t in STEPS], "", "Answer:"]
    return "\n".join(lines)


def findings_of(facts: dict[str, Any]) -> list[str]:
    return [
        f"{i.get('label', '')} {' '.join(redact(str(x)) for x in (i.get('excerpts') or [])[:2])} "
        f"{str(i.get('code', '')).replace('_', ' ').lower()}"
        for i in _ranked(facts)
    ]


@dataclass(frozen=True)
class LlmResult:
    status: str  # available | not_configured | unavailable | timeout | error | rejected
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
        findings = findings_of(facts)
        if not findings:
            return LlmResult("skipped", None, "no_findings_to_rephrase")
        if self._sem is None:
            self._sem = asyncio.Semaphore(1)
        prompt = build_prompt(facts)
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
        if not isinstance(text, str):
            return LlmResult("error", None, "bad_response")
        review = g.review_output(text, prompt, findings, max_len=MAX_LLM_OUTPUT)
        if review.text is None:
            log.info("llm explanation rejected: %s", review.reason)  # reason only, never text
            return LlmResult("rejected", None, review.reason)
        return LlmResult("available", review.text)

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

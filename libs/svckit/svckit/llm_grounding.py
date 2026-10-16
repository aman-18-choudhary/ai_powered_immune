"""Grounding checks for text written by a LOCAL language model (Ollama) about computed findings.

The model output is untrusted. Callers give it a findings-only prompt and pass its answer through
``review_output``; anything that fails is suppressed (the caller falls back to its deterministic
rule explanation and records ``rejected`` with the reason). The model never changes a score, level
or category; these checks only decide whether its wording may be shown.

Steps (``review_output``): empty -> ``empty_or_too_short``; longer than ``max_len`` ->
``output_too_long``; links / addresses / numbers that are not in the prompt are stripped
(``strip_invented``); complete, non-repeated sentences only (``tidy``); then ``validate_output``
(``unsafe_content``, ``likelihood_claim``) and ``ground_output``: ``wrong_language``,
``empty_or_too_short``, ``unsupported_claim`` (certainty, safety, "has evidence"),
``role_inversion`` (the caller / scammer / sender / officer described as the one under arrest,
investigated, threatened, the victim or asked to pay), ``ungrounded_indicator`` (a scam cue not
among the findings), ``not_grounded`` (refers to none of the findings).

These are keyword and pattern heuristics, not understanding: an accepted text can still be
clumsy or imprecise, which is why callers label it "LLM-generated (model), may be imprecise".

``is_local_url`` decides whether an Ollama URL is a local host (loopback, ``*.localhost``, a
private or link-local IP, or a single-label compose service name); callers never contact others.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_OUTPUT = 700  # characters; longer output is rejected, not truncated
MAX_SENTENCES = 3

URL = re.compile(
    r"https?://\S+|www\.\S+|"
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|in|xyz|top|click|info|co|io|me|ly|app|site|"
    r"online|icu|buzz|cc|link|gov|edu)\b\S*",
    re.IGNORECASE,
)
EMAIL_UPI = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{2,}")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,}\d")
DIGITS = re.compile(r"\d{5,}")
CLAIM = re.compile(r"probab|likelihood|\bchances?\b|\b\d{1,3}\s?%|percent", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\[(?:link|number|id|payment-id)\]")
_SENT = re.compile(r"[^.!?。！？]+[.!?。！？]+")


def validate_output(text: object, prompt: str, max_len: int = MAX_OUTPUT) -> str | None:
    """Safety check. Return a rejection reason, or None when the text is acceptable."""
    if not isinstance(text, str) or not text.strip():
        return "bad_response"
    if len(text) > max_len:
        return "output_too_long"
    for rx in (URL, EMAIL_UPI, PHONE, DIGITS):
        for m in rx.finditer(text):
            if m.group(0) not in prompt:
                return "unsafe_content"
    norm_prompt = " ".join(re.sub(r"[^\w%\s]", " ", prompt.casefold()).split())
    for m in CLAIM.finditer(text):
        # a phrase copied from the given steps ("the better the chance of a freeze") is allowed
        words = re.sub(r"[^\w%\s]", " ", text[m.start() :].casefold()).split()[:4]
        if " ".join(words) not in norm_prompt:
            return "likelihood_claim"
    return None


def tidy(text: str, max_sentences: int = MAX_SENTENCES) -> str:
    """Keep complete sentences only (a token cap can cut the last one), drop repeated sentences
    (small models loop) and keep at most ``max_sentences``. Never adds words."""
    # a small model often echoes the prompt as a list: keep the items, drop echoed headings
    text = re.split(r"(?im)^\s*(?:recommended steps|findings|answer)\s*:", text)[0]
    text = re.sub(r"(?m)^\s*(?:\d{1,2}[.)]|[-*•])\s+", " ", text)
    out: list[str] = []
    seen: set[str] = set()
    for m in _SENT.finditer(text):
        if len(m.group(0).strip()) < 8:  # list numbers such as "2." left after a cut
            continue
        s = " ".join(m.group(0).split())
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) == max_sentences:
            break
    return " ".join(out)


def strip_invented(text: str, prompt: str) -> str:
    """Remove links, addresses, phone numbers and digit runs that are not in the prompt, and the
    redaction placeholders the prompt itself used."""
    for rx in (URL, EMAIL_UPI, PHONE, DIGITS):
        text = rx.sub(lambda m: m.group(0) if m.group(0) in prompt else "", text)
    text = _PLACEHOLDER.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ------------------------------------------------------------------------- role inversion
# Evaluated per sentence on a normalised word list (case, quotes and punctuation ignored). Starting
# at a role word (the caller and the other parties of a scam script), the next words are scanned:
# * a reporting / speech verb (says, claims, told, asks, ...) or an object (you, the victim, ...)
#   ends the scan: what follows is about someone else (accepted);
# * an auxiliary (is, was, has been, will be, must, ...) followed by a victim predicate (asked,
#   told, threatened, arrested, investigated, "the victim", pay / transfer) is an inversion;
# * "under" / "in" (after an auxiliary) followed by arrest, custody or investigation, and "faces
#   arrest", are inversions;
# * an active verb such as "threatened" or "asked" without an auxiliary makes the role the agent
#   ("the caller threatened you"): accepted.
_ROLES = frozenset(
    "caller callers scammer scammers sender senders officer officers fraudster fraudsters "
    "impostor impostors imposter imposters".split()
)
_REPORTING = frozenset(
    "say says said saying claim claims claimed claiming state states stated tell tells telling "
    "warn warns warned warning insist insists insisted pretend pretends pretended pretending "
    "demand demands demanded demanding want wants wanted threaten threatens threatening ask asks "
    "asking urge urges urged order orders ordered instruct instructs instructed".split()
)
_OBJECTS = frozenset(
    "you your yours yourself victim victims user users person people complainant reporter "
    "target him her them me us someone anyone family recipient listener".split()
)
_AUX = frozenset(
    "is are was were be been being has have had will would shall gets got get must need needs "
    "should".split()
)
# participles that, after an auxiliary, make the role the one acted upon; without an auxiliary the
# same word is an active verb with the role as the agent (scan ends, accepted)
_PASSIVE = frozenset(
    "asked told threatened arrested investigated detained charged accused forced pressured "
    "pressurised pressurized blackmailed extorted made instructed ordered".split()
)
_PAY = frozenset("pay transfer send deposit".split())
_HELD = frozenset("arrest custody investigation detention".split())
_HELD_FILLER = frozenset("a an the digital police cbi judicial".split())
_VICTIM_FILLER = frozenset("the a an real actual also now true one".split())
_SCAN = 8


def _words(sentence: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", sentence.casefold()).split()


def _inverted(words: list[str], i: int) -> bool:
    aux = False
    for j in range(i + 1, min(len(words), i + 1 + _SCAN)):
        w = words[j]
        if w in ("victim", "victims") and aux:
            between = words[i + 1 : j]
            if all(x in _AUX or x in _VICTIM_FILLER for x in between):
                return True  # "the caller is the victim"
        if w in _OBJECTS or w in _REPORTING:
            return False
        if w in _AUX:
            aux = True
            continue
        if w in _PASSIVE:
            return aux
        if w in _PAY and aux:
            return True  # "the caller has to pay"
        if w in ("under", "in") and (aux or w == "under"):
            # "under (a) (digital) arrest", "in police custody", "under investigation"
            nxt = [x for x in words[j + 1 : j + 4] if x not in _HELD_FILLER]
            if nxt and nxt[0] in _HELD:
                return True
        if w in ("faces", "facing", "face") and any(x in _HELD for x in words[j + 1 : j + 3]):
            return True
    return False


def role_inversion(text: str) -> bool:
    """True when a sentence describes the caller (or scammer, sender, officer) as the one under
    arrest, investigated, threatened, the victim or asked to pay."""
    for sentence in re.split(r"[.!?;:。！？\n]+", text):
        words = _words(sentence)
        if any(w in _ROLES and _inverted(words, i) for i, w in enumerate(words)):
            return True
    return False


# ------------------------------------------------------------------------- grounding
# Scam cues the model could name (urgency words are left out: the static steps say "urgent" and
# "immediately" themselves). A cue is "ungrounded" when the output names it but none of its words
# appear in the findings (or the context, e.g. the suggested category).
CUE_WORDS: dict[str, tuple[str, ...]] = {
    "digital_arrest": ("digital arrest",),
    "safe_account": ("safe account", "rbi account", "verification account"),
    "isolation": ("stay on the call", "tell anyone", "tell no one", "keep it secret"),
    "authority": ("cbi", "police", "customs", "narcotics", "enforcement directorate", "officer"),
    "legal_threat": ("warrant", "fir", "arrest", "jail", "laundering", "legal action", "seized"),
    "credentials": ("otp", "cvv", "password", "pin"),
    "kyc": ("kyc",),
    "short_link": ("shortened", "short link", "shortener"),
    "lookalike": ("look-alike", "lookalike", "imitat", "punycode"),
    "prize": ("lottery", "prize", "reward", "cashback"),
    "remote_access": ("anydesk", "teamviewer", "screen shar", "remote access"),
}
UNSUPPORTED = re.compile(
    r"\b(?:has|have|had|holds?|with)\s+(?:\w+\s+){0,2}(?:evidence|proof)\b|\bconfirmed\b|"
    r"\bproven\b|\bdefinitely\b|\bcertainly\b|\bguarantee|\b(?:is|are|looks?)\s+"
    r"(?:\w+\s+)?(?:safe|legitimate|genuine)\b|\bnot\s+a\s+(?:scam|fraud)\b",
    re.IGNORECASE,
)
_STOP = frozenset(
    "about above after again their there these those which would could should other being "
    "where while asked asks message person money".split()
)


def _has(word: str, text: str) -> bool:
    w = word.strip()
    return re.search(rf"(?<![a-z]){re.escape(w)}", text, re.IGNORECASE) is not None and (
        re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text, re.IGNORECASE) is not None
        or w.endswith(("imitat", "screen shar"))
    )


def content_words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{5,}", t.casefold()) if w not in _STOP}


def ground_output(
    text: str, findings: Sequence[str], context: str = "", lang: str = "en"
) -> str | None:
    """Return a rejection reason, or None when ``text`` only rephrases ``findings``.

    ``findings``: one string per finding (its plain text, evidence snippet and code words).
    ``context``: extra text in which a named scam cue counts as grounded (e.g. the suggested
    category) but which does not count as a finding."""
    letters = [c for c in text if c.isalpha()]
    if lang == "en" and letters and sum(c.isascii() for c in letters) / len(letters) < 0.9:
        return "wrong_language"
    if len(text) < 20:
        return "empty_or_too_short"
    if UNSUPPORTED.search(text):
        return "unsupported_claim"
    if role_inversion(text):
        return "role_inversion"
    known = " ".join(findings) + " " + context
    for words in CUE_WORDS.values():
        if any(_has(w, text) for w in words) and not any(_has(w, known) for w in words):
            return "ungrounded_indicator"
    if findings:
        given: set[str] = set()
        for f in findings:
            given |= content_words(f)
        if not (content_words(text) & given):
            return "not_grounded"
    return None


@dataclass(frozen=True)
class Review:
    text: str | None  # the cleaned text when accepted, else None
    reason: str | None  # rejection reason when rejected, else None


def review_output(
    raw: object,
    prompt: str,
    findings: Sequence[str],
    context: str = "",
    lang: str = "en",
    max_len: int = MAX_OUTPUT,
    max_sentences: int = MAX_SENTENCES,
) -> Review:
    """The whole check for one model answer: shape, cleanup, safety, grounding."""
    if not isinstance(raw, str):
        return Review(None, "bad_response")
    if not raw.strip():
        return Review(None, "empty_or_too_short")
    if len(raw) > max_len:
        return Review(None, "output_too_long")
    text = tidy(strip_invented(raw.strip(), prompt), max_sentences)
    why = validate_output(text, prompt, max_len) or ground_output(text, findings, context, lang)
    if why:
        return Review(None, "empty_or_too_short" if why == "bad_response" else why)
    return Review(text, None)


def is_local_url(url: str | None) -> bool:
    """Loopback, ``*.localhost``, a private or link-local IP literal, or a single-label host name
    (a container on the same compose network, e.g. ``ollama``)."""
    if not url:
        return False
    try:
        host = (urlsplit(url).hostname or "").casefold()
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

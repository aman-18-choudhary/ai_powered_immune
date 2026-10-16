"""Chunk pipeline: validated PCM -> STT -> call-guard analysis -> risk, indicators, warnings.

This is incremental, chunked processing (``mode: incremental_chunked``), not streaming
recognition: each uploaded chunk (default 4 s) is transcribed as a whole, so a word is
reported at the earliest one chunk length plus processing time after it was spoken.

Logging: only sequence numbers, durations, counts and status codes; never transcript text,
excerpts, session ids or audio.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .config import LiveAudioConfig
from .detect import (
    LEVELS,
    SCORE_SEMANTICS,
    STATEMENT,
    LiveDetector,
    ScorerUnavailable,
    level_for,
)
from .explain import OllamaExplainer, rules_explanation
from .guidance import actions_for
from .session import IndicatorState, LiveSession, RateLimiter, SessionManager
from .stt import LANGUAGES, SttEngine, SttRunner, SttUnavailable
from .wav import BYTES_PER_SECOND, SAMPLE_RATE

log = logging.getLogger("live_audio")

WARNING_TEXT = {
    "MEDIUM": "Some warning signs were heard. Be careful and do not share codes or send money.",
    "HIGH": "Strong scam indicators were heard. Do not pay or share anything; consider hanging up.",
    "CRITICAL": "Several serious scam indicators were heard. Hang up and call your bank or 1930.",
}
ERROR_TEXT = {
    "stt": "Speech could not be transcribed for this chunk; risk was not updated.",
    "analysis": "The transcript could not be analysed for this chunk; risk was not updated.",
}
HISTORY_TAIL = 30
_WORD = re.compile(r"[\w']+", re.UNICODE)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def strip_overlap(prev_words: list[str], text: str, max_k: int = 4) -> str:
    """Drop leading words of ``text`` that repeat the last words heard (tail overlap)."""
    toks = text.split()
    if not prev_words or not toks:
        return text
    norm = [(_words(t) or [""])[0] for t in toks]
    for k in range(min(max_k, len(norm), len(prev_words)), 0, -1):
        if norm[:k] == prev_words[-k:]:
            return " ".join(toks[k:])
    return text


class LiveAudioService:
    def __init__(
        self,
        cfg: LiveAudioConfig,
        engine: SttEngine,
        scorer_provider: Callable[[], Any],
        *,
        explainer: OllamaExplainer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.engine = engine
        self.runner = SttRunner(engine, cfg.stt_concurrency, cfg.stt_timeout_s)
        self.scorer_provider = scorer_provider
        self.detector = LiveDetector(scorer_provider)
        self.explainer = explainer or OllamaExplainer(
            cfg.ollama_url, cfg.ollama_model, cfg.ollama_timeout_s
        )
        self.clock = clock
        self.sessions = SessionManager(
            clock, max_sessions=cfg.max_sessions, max_per_user=cfg.max_sessions_per_user,
            idle_ttl_s=cfg.session_idle_ttl_s, on_drop=self._on_drop,
        )  # fmt: skip
        self.limiter = RateLimiter(clock)
        self._preload: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ lifecycle
    def _on_drop(self, s: LiveSession) -> None:
        if s.llm_task and not s.llm_task.done():
            s.llm_task.cancel()
        s.transcript.clear()
        s.results.clear()

    def ensure_preload(self) -> None:
        """Load the STT model in a worker thread (first session creation), once."""
        if self._preload is None and self.engine.status().get("status") == "not_loaded":
            self._preload = asyncio.get_running_loop().create_task(
                asyncio.to_thread(self.engine.preload)
            )

    async def aclose(self) -> None:
        self.sessions.clear()
        if self._preload and not self._preload.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._preload), 30)
            except Exception:
                pass

    # ---------------------------------------------------------------- capabilities
    async def capabilities(self) -> dict[str, Any]:
        st = self.engine.status()
        scorer = self.scorer_provider()
        llm = await self.explainer.probe()
        return {
            "mode": "incremental_chunked",
            "mode_note": (
                "Audio is uploaded in short chunks and each chunk is transcribed and analysed "
                "when it arrives. This is near-real-time incremental processing, not streaming "
                "recognition."
            ),
            "chunk_ms": self.cfg.chunk_ms,
            "max_chunk_ms": self.cfg.max_chunk_ms,
            "max_chunk_bytes": self.cfg.max_chunk_bytes,
            "audio": {"sample_rate": SAMPLE_RATE, "channels": 1, "bits": 16, "container": "wav"},
            "stt": {
                "engine": self.engine.name,
                "model": self.engine.model,
                "status": st.get("status"),
                "reason": st.get("reason"),
                "languages": list(LANGUAGES),
                "local": True,
            },  # fmt: skip
            "scorer": {
                "status": "ready" if scorer is not None else "unavailable",
                "model_version": getattr(scorer, "model_version", None),
                "fallback_mode": bool(getattr(scorer, "fallback_mode", False)),
            },
            "llm": {
                "status": llm,
                "model": self.explainer.model if self.explainer.configured else None,
                "role": "explanation only; never changes score or level",
            },
            "limits": {
                "sessions_per_user": self.cfg.max_sessions_per_user,
                "chunks_per_minute": self.cfg.chunks_per_minute,
                "session_idle_ttl_s": self.cfg.session_idle_ttl_s,
            },
            "privacy": {
                "raw_audio_stored": False,
                "transcript_persisted": False,
                "transcript_logged": False,
                "note": "Audio is held in memory only while a chunk is transcribed (plus a 0.5 s "
                "overlap tail); the transcript lives in server memory until you stop or the "
                "session expires.",
            },
            "score_semantics": SCORE_SEMANTICS,
        }

    # --------------------------------------------------------------------- views
    def risk_view(self, s: LiveSession) -> dict[str, Any]:
        assessed = s.assessed_chunks > 0
        score = s.risk.score if assessed else None
        status = "stale" if s.stale else ("assessed" if assessed else "not_assessed")
        return {
            "score": score,
            "level": s.level if assessed else None,
            "status": status,
            "stale": s.stale,
            "assessed_chunks": s.assessed_chunks,
            "history_tail": list(s.history)[-HISTORY_TAIL:],
            "score_semantics": SCORE_SEMANTICS,
            "levels": {"LOW": "0-24", "MEDIUM": "25-49", "HIGH": "50-69", "CRITICAL": "70-100"},
            "statement": STATEMENT,
            "model_version": s.model_version,
        }

    def _facts(self, s: LiveSession) -> dict[str, Any]:
        r = self.risk_view(s)
        return {
            "level": r["level"],
            "score": r["score"],
            "indicators": [
                {"code": i.code, "label": i.label, "excerpts": i.excerpts}
                for i in sorted(s.indicators.values(), key=lambda i: -i.weight)
            ],
        }

    def explanation_view(self, s: LiveSession) -> dict[str, Any]:
        configured = self.explainer.configured
        return {
            "rules": rules_explanation(self._facts(s)),
            "llm": s.llm_text,
            "llm_status": s.llm_status if configured else "not_configured",
            "llm_reason": s.llm_reason if configured else "not_configured",
            "llm_at_s": s.llm_at_s,
            "llm_model": s.llm_model if s.llm_text else None,
            # same wording as the complaints analysis; the rules text above stays primary
            "llm_label": f"LLM-generated ({s.llm_model}), may be imprecise" if s.llm_text else None,
            "llm_note": "Generated by a local model from the indicators only; it does not "
            "change the score or level."
            if s.llm_text
            else None,
        }

    def _indicators(self, s: LiveSession) -> list[dict[str, Any]]:
        return [i.view() for i in sorted(s.indicators.values(), key=lambda i: (-i.weight, i.code))]

    def actions(self, s: LiveSession) -> list[dict[str, Any]]:
        return actions_for(set(s.indicators), s.level)

    def state_view(self, s: LiveSession) -> dict[str, Any]:
        return {
            "session_id": s.id,
            "language": s.language,
            "started_at": s.wall_started,
            "mode": "incremental_chunked",
            "next_seq": s.next_seq,
            "chunks_processed": len(s.processed),
            "gaps": s.gaps(),
            "transcript": list(s.transcript),
            "transcript_truncated": s.truncated,
            "indicators": self._indicators(s),
            "risk": self.risk_view(s),
            "warnings": list(s.warnings),
            "actions": self.actions(s),
            "explanation": self.explanation_view(s),
            "errors": list(s.errors),
            "status": dict(s.last_status),
        }

    def summary_view(self, s: LiveSession) -> dict[str, Any]:
        return {
            "ended": True,
            "session_id": s.id,
            "chunks_processed": len(s.processed),
            "audio_seconds": round(s.audio_end_s, 2),
            "segments": len(s.transcript),
            "indicators": self._indicators(s),
            "risk": self.risk_view(s),
            "warnings": list(s.warnings),
            "actions": self.actions(s),
            "statement": STATEMENT,
            "retained": "nothing: session, transcript and indicators deleted from server memory",
        }

    # ------------------------------------------------------------------ pipeline
    async def process_chunk(
        self, s: LiveSession, seq: int, pcm: bytes, start_ms: int | None
    ) -> dict[str, Any]:
        async with s.lock:
            if seq in s.results:
                return {**s.results[seq], "duplicate": True}
            if seq in s.processed:  # processed long ago, response evicted from the cache
                return {"seq": seq, "duplicate": True, "segments": [], "warnings": [],
                        "indicators": self._indicators(s), "risk": self.risk_view(s),
                        "status": {"stt": "duplicate", "analysis": "duplicate"}, "errors": [],
                        "gaps": s.gaps(), "late": False, "actions": self.actions(s),
                        "explanation": self.explanation_view(s), "timing": {}}  # fmt: skip
            res = await self._process(s, seq, pcm, start_ms)
            s.cache(seq, res)
            return res

    async def _process(
        self, s: LiveSession, seq: int, pcm: bytes, start_ms: int | None
    ) -> dict[str, Any]:
        t0 = time.monotonic()
        late = seq < s.next_seq
        dur = len(pcm) / BYTES_PER_SECOND
        if start_ms is not None:
            start_s = start_ms / 1000.0
        elif not late:
            start_s = s.audio_end_s
        else:
            start_s = seq * self.cfg.chunk_ms / 1000.0
        tail_s = len(s.tail_pcm) / BYTES_PER_SECOND
        use_tail = bool(s.tail_pcm) and s.tail_seq == seq - 1 and abs(start_s - s.tail_end_s) < 0.25
        audio = s.tail_pcm + pcm if use_tail else pcm
        offset = start_s - (tail_s if use_tail else 0.0)
        status = {"stt": "ok", "analysis": "ok", "llm": "idle"}
        errors: list[dict[str, Any]] = []
        new_segments: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        stt_ms = analysis_ms = 0

        # ---- STT
        t1 = time.monotonic()
        stt = None
        try:
            stt = await self.runner.transcribe(audio, SAMPLE_RATE, s.language)
        except SttUnavailable as e:
            status["stt"] = e.status
            errors.append(self._error(s, seq, "stt", e.reason))
        except Exception as e:  # engine bug: report, never crash the session
            status["stt"] = "error"
            errors.append(self._error(s, seq, "stt", "engine_error"))
            log.warning("stt engine error type=%s", type(e).__name__)
        stt_ms = int((time.monotonic() - t1) * 1000)
        del audio
        if not late:  # keep only the overlap tail of the newest chunk
            keep = int(self.cfg.tail_s * BYTES_PER_SECOND) & ~1
            s.tail_pcm = pcm[-keep:] if keep else b""
            s.tail_seq, s.tail_end_s = seq, start_s + dur
        del pcm

        # ---- segments
        if stt is not None:
            prev_words = s.last_words
            for seg in stt.segments:
                text = seg.text
                if use_tail:
                    if seg.end_s <= tail_s + 0.05:
                        continue  # entirely inside the overlap: already transcribed
                    if seg.start_s < tail_s:
                        text = strip_overlap(prev_words, text)
                if not text.strip():
                    continue
                s.seg_counter += 1
                item = {
                    "id": s.seg_counter, "seq": seq, "start_s": round(offset + seg.start_s, 2),
                    "end_s": round(offset + seg.end_s, 2), "text": text,
                    "language": seg.language, "indicator_codes": [],
                    "confidence": None if seg.avg_logprob is None else round(seg.avg_logprob, 3),
                }  # fmt: skip
                new_segments.append(item)
            if new_segments and not late:
                s.last_words = _words(new_segments[-1]["text"])[-4:]
            self._add_transcript(s, new_segments)

        # ---- analysis
        prev_level = s.level if s.assessed_chunks else None
        if stt is None:
            status["analysis"] = "skipped"
            s.stale = True
        elif not new_segments:
            status["analysis"] = "no_speech"
        else:
            t2 = time.monotonic()
            try:
                a = await asyncio.to_thread(
                    self.detector.analyse, [(x["id"], x["text"]) for x in new_segments]
                )
            except ScorerUnavailable as e:
                status["analysis"] = "unavailable"
                s.stale = True
                errors.append(self._error(s, seq, "analysis", str(e)))
            else:
                at_s = start_s + dur
                score = s.risk.update(a.score, a.reasons, at_s)
                s.assessed_chunks += 1
                s.stale = False
                s.model_version = a.model_version
                s.level = level_for(score)
                s.history.append(
                    {"seq": seq, "at_s": round(at_s, 2), "score": score, "level": s.level}
                )
                by_id = {x["id"]: x for x in new_segments}
                for h in a.hits:
                    seg = by_id.get(h.segment_id)
                    if seg is None:
                        continue
                    if h.code not in seg["indicator_codes"]:
                        seg["indicator_codes"].append(h.code)
                    ind = s.indicators.get(h.code)
                    if ind is None:
                        ind = s.indicators[h.code] = IndicatorState(
                            h.code, h.label, h.source, h.weight, first_at_s=seg["start_s"]
                        )
                    ind.weight = max(ind.weight, h.weight)
                    if h.segment_id not in ind.segment_ids and len(ind.segment_ids) < 50:
                        ind.segment_ids.append(h.segment_id)
                    if h.excerpt and h.excerpt not in ind.excerpts and len(ind.excerpts) < 3:
                        ind.excerpts.append(h.excerpt)
                    ind.last_at_s = max(ind.last_at_s, seg["end_s"])
                if prev_level is None or LEVELS.index(s.level) > LEVELS.index(prev_level):
                    if s.level != "LOW":
                        w = {
                            "id": len(s.warnings) + 1, "seq": seq, "at_s": round(at_s, 2),
                            "wall": datetime.now(UTC).isoformat(timespec="seconds"),
                            "level": s.level, "previous_level": prev_level, "score": score,
                            "codes": sorted({h.code for h in a.hits}),
                            "message": WARNING_TEXT[s.level],
                        }  # fmt: skip
                        s.warnings.append(w)
                        warnings.append(w)
            analysis_ms = int((time.monotonic() - t2) * 1000)

        # ---- bookkeeping
        s.processed.add(seq)
        s.next_seq = max(s.next_seq, seq + 1)
        if not late:
            s.audio_end_s = max(s.audio_end_s, start_s + dur)
        status["llm"] = self._maybe_explain(s)
        s.last_status = dict(status)
        total_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "chunk seq=%d dur_ms=%d stt=%s analysis=%s segments=%d stt_ms=%d total_ms=%d",
            seq, int(dur * 1000), status["stt"], status["analysis"], len(new_segments), stt_ms,
            total_ms,
        )  # fmt: skip
        return {
            "seq": seq,
            "duplicate": False,
            "late": late,
            "segments": new_segments,
            "indicators": self._indicators(s),
            "risk": self.risk_view(s),
            "warnings": warnings,
            "actions": self.actions(s),
            "explanation": self.explanation_view(s),
            "status": status,
            "errors": errors,
            "gaps": s.gaps(),
            "timing": {"stt_ms": stt_ms, "analysis_ms": analysis_ms, "total_ms": total_ms},
            "engine": {"name": self.engine.name, "model": self.engine.model},
        }

    def _error(self, s: LiveSession, seq: int, stage: str, code: str) -> dict[str, Any]:
        e = {
            "seq": seq, "stage": stage, "code": code, "message": ERROR_TEXT[stage],
            "wall": datetime.now(UTC).isoformat(timespec="seconds"),
        }  # fmt: skip
        s.errors.append(e)
        return e

    def _add_transcript(self, s: LiveSession, segs: list[dict[str, Any]]) -> None:
        if not segs:
            return
        s.transcript.extend(segs)
        s.transcript.sort(key=lambda x: (x["start_s"], x["id"]))
        s.transcript_chars += sum(len(x["text"]) for x in segs)
        while s.transcript and (
            len(s.transcript) > self.cfg.max_segments
            or s.transcript_chars > self.cfg.max_transcript_chars
        ):
            old = s.transcript.pop(0)
            s.transcript_chars -= len(old["text"])
            s.truncated = True

    # ------------------------------------------------------------------- LLM
    def _maybe_explain(self, s: LiveSession) -> str:
        if not self.explainer.configured:
            return "not_configured"
        if s.llm_task is not None and not s.llm_task.done():
            return "pending"
        lvl = s.level if s.assessed_chunks else None
        now = self.clock()
        if lvl is None or lvl == s.llm_level or now - s.llm_last < self.cfg.llm_min_interval_s:
            return s.llm_status
        s.llm_last, s.llm_level, s.llm_status = now, lvl, "pending"
        facts = self._facts(s)
        at_s = s.audio_end_s

        async def run() -> None:
            r = await self.explainer.explain(facts)
            s.llm_status, s.llm_reason = r.status, r.reason
            # a rejected / failed answer for the new findings replaces the older text: the
            # console then shows only the rules explanation (never stale LLM wording)
            s.llm_text = r.text if r.status == "available" else None
            s.llm_model = self.explainer.model if s.llm_text else None
            s.llm_at_s = round(at_s, 2) if s.llm_text else None

        s.llm_task = asyncio.get_running_loop().create_task(run())
        return "pending"

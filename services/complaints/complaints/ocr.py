"""Text of screenshot evidence (Phase 6, OCR): read locally with Tesseract when the complaint
analysis runs, stored once per file (``evidence_ocr``), then scored by the same rules as the
description.

* Engine: the ``tesseract`` program (5.x) on this server, languages ``eng`` and ``hin`` when their
  data files are installed. Nothing is sent anywhere; no network is used.
* Types: PNG, JPEG and WebP images. PDFs are not read (``unsupported_type``).
* Bounds: at most ``max_files`` new files per analysis, ``timeout_s`` per file, a run budget;
  the stored text is capped at ``MAX_TEXT`` characters.
* OCR makes mistakes (low contrast, small fonts, mixed scripts). Each result carries the mean word
  confidence Tesseract reports, and the console says the text was machine-read.

Statuses: ``extracted`` (text found), ``no_text`` (nothing readable), ``unsupported_type``,
``failed``, ``timeout``, ``file_missing``, ``engine_unavailable``, ``budget_exhausted`` (not
read this time; tried again next run). Text is user content: never logged.
"""

import asyncio
import contextlib
import csv
import io
import logging
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("complaints.ocr")

OCR_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_TEXT = 5000
STORED = frozenset({"extracted", "no_text", "unsupported_type", "failed", "timeout"})
STATUS_TEXT = {
    "extracted": "Text was read from the image by OCR (machine-read; it may contain errors).",
    "no_text": "No readable text was found in the image.",
    "unsupported_type": "This file type is not read by OCR (PNG, JPEG and WebP images only).",
    "failed": "The image could not be read.",
    "timeout": "Reading the image took too long and was stopped.",
    "file_missing": "The stored file is not available.",
    "engine_unavailable": "No OCR engine is installed on this server.",
    "budget_exhausted": "Not read in this analysis (time or file limit); it is read next time.",
    "disabled": "Screenshot text reading is turned off on this server.",
}


def normalise_lines(lines: list[str]) -> str:
    """Join OCR lines: a line ending in a hyphen continues without a space (wrapped links)."""
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-"):
            out += line
        else:
            out += " " + line
    return out[:MAX_TEXT]


class TesseractEngine:
    def __init__(self, binary: str | None = None, langs: tuple[str, ...] = ("eng", "hin"),
                 timeout_s: float = 20.0) -> None:  # fmt: skip
        self.binary = binary or shutil.which("tesseract")
        self.requested = langs
        self.timeout_s = timeout_s
        self._langs: list[str] | None = None
        self._version: str | None = None

    def _probe(self) -> None:
        if self._langs is not None or not self.binary:
            return
        try:
            v = subprocess.run([self.binary, "--version"], capture_output=True, text=True,
                               timeout=10, check=False)  # fmt: skip
            self._version = (
                (v.stdout or v.stderr).splitlines()[0].strip() if (v.stdout or v.stderr) else None
            )
            r = subprocess.run([self.binary, "--list-langs"], capture_output=True, text=True,
                               timeout=10, check=False)  # fmt: skip
            have = {x.strip() for x in (r.stdout or "").splitlines()[1:] if x.strip()}
            self._langs = [x for x in self.requested if x in have]
        except (OSError, subprocess.SubprocessError):
            self._langs = []

    def status(self) -> dict[str, Any]:
        if not self.binary:
            return {"status": "unavailable", "reason": "engine_not_installed",
                    "engine": "tesseract",
                    "version": None, "languages": []}  # fmt: skip
        self._probe()
        if not self._langs:
            return {"status": "unavailable", "reason": "no_language_data", "engine": "tesseract",
                    "version": self._version, "languages": []}  # fmt: skip
        return {"status": "ready", "reason": None, "engine": "tesseract",
                "version": self._version, "languages": list(self._langs)}  # fmt: skip

    def read(self, path: Path) -> tuple[str, float | None]:
        """(text, mean word confidence 0-100 or None). Raises TimeoutError / RuntimeError."""
        self._probe()
        assert self.binary and self._langs
        try:
            r = subprocess.run(
                [self.binary, str(path), "stdout", "-l", "+".join(self._langs),
                 "--psm", "3", "tsv"],
                capture_output=True, text=True, timeout=self.timeout_s, check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            raise TimeoutError from None
        if r.returncode != 0:
            raise RuntimeError("tesseract failed")
        lines: dict[tuple[str, str, str], list[str]] = {}
        confs: list[float] = []
        for row in csv.DictReader(io.StringIO(r.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
            word = (row.get("text") or "").strip()
            if row.get("level") != "5" or not word:
                continue
            key = (row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
            lines.setdefault(key, []).append(word)
            with contextlib.suppress(ValueError, TypeError):
                c = float(row.get("conf") or -1)
                if c >= 0:
                    confs.append(c)
        text = normalise_lines([" ".join(ws) for ws in lines.values()])
        return text, (round(sum(confs) / len(confs), 1) if confs else None)


class NoOcr:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def status(self) -> dict[str, Any]:
        return {"status": "unavailable", "reason": self.reason, "engine": None, "version": None,
                "languages": []}  # fmt: skip

    def read(self, path: Path) -> tuple[str, float | None]:  # pragma: no cover - never called
        raise RuntimeError(self.reason)


def result(evidence_id: str, mime: str, status: str, **kw: Any) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "mime_type": mime, "status": status,
            "message": STATUS_TEXT.get(status, status), **kw}  # fmt: skip


class OcrService:
    def __init__(self, engine: Any, *, enabled: bool = True, max_files: int = 3,
                 budget_s: float = 45.0) -> None:  # fmt: skip
        self.engine = engine
        self.enabled = enabled
        self.max_files = max_files
        self.budget_s = budget_s
        self._sem = asyncio.Semaphore(1)  # one OCR process at a time on this server

    async def health(self) -> dict[str, Any]:
        st = await asyncio.to_thread(self.engine.status)
        if not self.enabled:
            return {**st, "status": "disabled", "reason": "disabled"}
        return st

    async def prepare(
        self,
        files: list[dict[str, Any]],
        path_for: Callable[[dict[str, Any]], Path],
        load_existing: Callable[[], Awaitable[dict[str, dict[str, Any]]]],
        save: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]],
    ) -> dict[str, dict[str, Any]]:
        """OCR results for every image evidence file (stored ones reused, new ones read now)."""
        images = [f for f in files if str(f.get("mime_type", "")).startswith("image/")]
        if not images:
            return {}
        existing = await load_existing()
        out: dict[str, dict[str, Any]] = {}
        st = await self.health()
        ready = st["status"] == "ready"
        deadline = time.monotonic() + self.budget_s
        done = 0
        for f in images:
            eid, mime = f["id"], f["mime_type"]
            if eid in existing:
                out[eid] = existing[eid]
                continue
            if mime not in OCR_MIMES:
                rec = result(eid, mime, "unsupported_type")
            elif not self.enabled:
                out[eid] = result(eid, mime, "disabled")
                continue
            elif not ready:
                out[eid] = result(eid, mime, "engine_unavailable", reason=st.get("reason"))
                continue
            elif done >= self.max_files or time.monotonic() >= deadline:
                out[eid] = result(eid, mime, "budget_exhausted")
                continue
            else:
                path = path_for(f)
                if not await asyncio.to_thread(path.is_file):
                    out[eid] = result(eid, mime, "file_missing")
                    continue
                done += 1
                async with self._sem:
                    try:
                        text, conf = await asyncio.to_thread(self.engine.read, path)
                        rec = result(eid, mime, "extracted" if text.strip() else "no_text",
                                     text=text, mean_confidence=conf,
                                     languages=st.get("languages"))  # fmt: skip
                    except TimeoutError:
                        rec = result(eid, mime, "timeout")
                    except Exception as exc:
                        log.warning("ocr failed: %s", type(exc).__name__)
                        rec = result(eid, mime, "failed")
                rec["engine"] = f"tesseract {st.get('version') or ''}".strip()
            saved = await save(rec)
            out[eid] = saved or rec
        return out


def engine_from_env(enabled: bool = True) -> Any:
    if not enabled:
        return NoOcr("disabled")
    eng = TesseractEngine()
    return eng if eng.binary else NoOcr("engine_not_installed")

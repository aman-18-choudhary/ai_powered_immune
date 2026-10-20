"""Evidence file handling: magic-byte sniffing, filename sanitising, streamed storage.

Guarantees (see README for the limits): the type is decided from the first bytes, not from the
extension or Content-Type; the byte cap is enforced while streaming (memory use is O(chunk));
the file is written to a temp name, fsynced, atomically renamed, re-read and hash-verified before
the caller records a ``stored`` row; the storage key is random and the client filename never
becomes part of a path. There is NO malware scanning.
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import logging
import os
import re
import secrets
import time
import unicodedata
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

HEAD_BYTES = 16
MAX_FILENAME = 100
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".pdf": "application/pdf", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4",
}  # fmt: skip
_M4A_BRANDS = (b"M4A ", b"M4B ")


class UploadError(Exception):
    """A refusal with an HTTP status and stable code (never carries file content)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def sniff(head: bytes) -> tuple[str, str] | None:
    """(mime, kind) from the leading bytes; kind is 'image' | 'pdf' | 'audio'. None if unknown."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav", "audio"
    if head.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if head.startswith(b"OggS"):
        return "audio/ogg", "audio"
    if head.startswith(b"ID3"):
        return "audio/mpeg", "audio"
    if head[4:8] == b"ftyp" and head[8:12] in _M4A_BRANDS:
        return "audio/mp4", "audio"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        version, layer = (head[1] >> 3) & 3, (head[1] >> 1) & 3
        if version != 1 and layer != 0:  # MPEG audio frame sync, reserved values excluded
            return "audio/mpeg", "audio"
    return None


_FILENAME_BAD = re.compile(r"[^A-Za-z0-9._ -]")


def sanitise_filename(raw: str) -> str:
    """Basename only, ASCII-safe, no control chars, no dot-dot, bounded. Display/audit only."""
    name = unicodedata.normalize("NFKC", raw)
    name = re.split(r"[\\/]", name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = _FILENAME_BAD.sub("_", name).replace("..", "_").strip(" .")
    stem, dot, ext = name.rpartition(".")
    if len(name) > MAX_FILENAME:
        ext = ext[:10] if dot else ""
        name = (stem if dot else name)[: MAX_FILENAME - len(ext) - (1 if ext else 0)]
        name = f"{name}.{ext}" if ext else name
    return name or "file"


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    sha256: str
    size_bytes: int
    mime_type: str
    original_filename: str
    kind: str
    # malware scan before storing: clean, not_scanned (scanner unreachable, not required) or
    # None when no scanner is configured
    scan_status: str | None = None


log = logging.getLogger("complaints.evidence")

MAGIC = b"SSEV1\x00"  # encrypted evidence file: MAGIC + 12-byte nonce + AES-256-GCM ciphertext
NONCE_BYTES = 12


class EvidenceUnreadable(Exception):
    """An encrypted file without the right key, or a damaged one."""


class EvidenceStorage:
    """Evidence files on local disk under random names. With ``key`` (32 bytes,
    ``COMPLAINTS_EVIDENCE_KEY``) new files are encrypted at rest with AES-256-GCM, the storage
    key bound as associated data; files written before (plaintext) stay readable."""

    def __init__(self, root: Path, key: bytes | None = None) -> None:
        self.root = root
        self.tmp = root / ".tmp"
        if key is not None and len(key) != 32:
            raise ValueError("the evidence key must be 32 bytes")
        self.key = key

    def prepare(self) -> None:
        """Create the directories (0700). Raises OSError if the directory is unusable."""
        for d in (self.root, self.tmp):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.tmp, 0o700)

    def sweep_tmp(self, max_age_s: float = 24 * 3600) -> int:
        n = 0
        cutoff = time.time() - max_age_s
        for p in self.tmp.glob("*"):
            with contextlib.suppress(OSError):
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
        return n

    def writable(self) -> bool:
        probe = self.tmp / f"probe-{secrets.token_hex(8)}"
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            probe.unlink()
            return True
        except OSError:
            return False

    def path_for(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("bad storage key")
        return self.root / key[:2] / key

    # ------------------------------------------------------------------ encryption at rest
    def _aead(self) -> Any:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        assert self.key is not None
        return AESGCM(self.key)

    def encrypt_bytes(self, name: str, plain: bytes) -> bytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        return MAGIC + nonce + self._aead().encrypt(nonce, plain, name.encode())

    def decrypt_bytes(self, name: str, blob: bytes) -> bytes:
        if not blob.startswith(MAGIC):
            return blob  # stored before encryption was enabled
        if self.key is None:
            raise EvidenceUnreadable("encrypted file and no evidence key configured")
        from cryptography.exceptions import InvalidTag

        body = blob[len(MAGIC):]
        try:
            return self._aead().decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], name.encode())
        except InvalidTag:
            raise EvidenceUnreadable("wrong key or damaged file") from None

    def is_encrypted(self, key: str) -> bool:
        with open(self.path_for(key), "rb") as f:
            return f.read(len(MAGIC)) == MAGIC

    def read_plain(self, key: str) -> bytes:
        return self.decrypt_bytes(key, self.path_for(key).read_bytes())

    @contextlib.contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        """A path with the plaintext: the file itself, or a 0600 temporary copy (encrypted
        files), deleted on exit. Raises EvidenceUnreadable / OSError."""
        path = self.path_for(key)
        if not path.is_file() or not self.is_encrypted(key):
            yield path
            return
        plain = self.read_plain(key)
        tmp = self.tmp / f"plain-{secrets.token_hex(16)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(plain)
            yield tmp
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def encrypt_existing(self, key: str, expected_sha256: str) -> bool:
        """Encrypt one plaintext file in place (atomic). False when already encrypted."""
        if self.key is None:
            raise EvidenceUnreadable("no evidence key configured")
        path = self.path_for(key)
        if self.is_encrypted(key):
            return False
        plain = path.read_bytes()
        if hashlib.sha256(plain).hexdigest() != expected_sha256:
            raise EvidenceUnreadable("the stored file does not match its recorded hash")
        tmp = self.tmp / f"enc-{secrets.token_hex(16)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self.encrypt_bytes(key, plain))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
        if hashlib.sha256(self.read_plain(key)).hexdigest() != expected_sha256:
            raise EvidenceUnreadable("verification after encryption failed")
        return True

    def remove(self, key: str) -> None:
        with contextlib.suppress(OSError, ValueError):
            self.path_for(key).unlink()

    def remove_checked(self, key: str) -> bool:
        """Delete the stored file; True when no file remains under the key afterwards."""
        try:
            path = self.path_for(key)
        except ValueError:
            return False
        try:
            path.unlink()
        except FileNotFoundError:
            pass  # already gone
        except OSError:
            return False
        return not path.exists()


def _rehash(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class _Sink:
    """Receives the single file part: sniff -> cap -> temp file -> hash while streaming."""

    def __init__(self, storage: EvidenceStorage, max_image: int, max_audio: int) -> None:
        self.storage, self.max_image, self.max_audio = storage, max_image, max_audio
        self.head = bytearray()
        self.decided: tuple[str, str] | None = None
        self.cap = 0
        self.fd: int | None = None
        self.tmp_path: Path | None = None
        self.hash = hashlib.sha256()
        self.size = 0
        self.filename = "file"

    def _decide(self) -> None:
        found = sniff(bytes(self.head))
        if found is None:
            raise UploadError(
                415, "unsupported_file_type",
                "Only PNG, JPEG, WebP, PDF, MP3, WAV, OGG and M4A files are accepted.",
            )  # fmt: skip
        ext = os.path.splitext(self.filename)[1].casefold()
        if ext and (ext not in ALLOWED_EXTENSIONS or ALLOWED_EXTENSIONS[ext] != found[0]):
            raise UploadError(
                415, "file_type_mismatch", "The file content does not match its name."
            )
        self.decided = found
        self.cap = self.max_audio if found[1] == "audio" else self.max_image
        self.tmp_path = self.storage.tmp / f"up-{secrets.token_hex(16)}"
        self.fd = os.open(self.tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        data = bytes(self.head)
        self.head.clear()
        self._write(data)

    def _write(self, data: bytes) -> None:
        assert self.fd is not None
        self.size += len(data)
        if self.size > self.cap:
            raise UploadError(413, "file_too_large", "The file exceeds the size limit.")
        self.hash.update(data)
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view) :]

    def feed(self, data: bytes) -> None:
        if self.decided is None:
            self.head += data
            if len(self.head) >= HEAD_BYTES:
                self._decide()
        else:
            self._write(data)

    def end_part(self) -> None:
        if self.decided is None:
            self._decide()

    def abort(self) -> None:
        if self.fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = None
        if self.tmp_path is not None:
            with contextlib.suppress(OSError):
                self.tmp_path.unlink()

    def commit(self) -> StoredFile:
        """fsync, atomic rename, re-read and verify. Raises OSError/UploadError on any failure."""
        assert self.fd is not None and self.tmp_path is not None and self.decided is not None
        os.fsync(self.fd)
        os.close(self.fd)
        self.fd = None
        key = secrets.token_hex(16)
        final = self.storage.path_for(key)
        final.parent.mkdir(mode=0o700, exist_ok=True)
        if self.storage.key is not None:
            # encrypt at rest: the ciphertext replaces the plaintext temp file before the rename
            plain_tmp = self.tmp_path
            enc_tmp = self.storage.tmp / f"enc-{secrets.token_hex(16)}"
            fd = os.open(enc_tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(self.storage.encrypt_bytes(key, plain_tmp.read_bytes()))
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                with contextlib.suppress(OSError):
                    plain_tmp.unlink()
            self.tmp_path = enc_tmp
        os.replace(self.tmp_path, final)
        self.tmp_path = None
        try:
            _fsync_dir(final.parent)
            if self.storage.key is not None:
                try:
                    plain = self.storage.read_plain(key)
                except EvidenceUnreadable:
                    raise UploadError(503, "evidence_storage_failed",
                                      "The file could not be stored.") from None  # fmt: skip
                digest, size = hashlib.sha256(plain).hexdigest(), len(plain)
            else:
                digest, size = _rehash(final)
            if digest != self.hash.hexdigest() or size != self.size:
                raise UploadError(503, "evidence_storage_failed", "The file could not be stored.")
        except BaseException:
            with contextlib.suppress(OSError):
                final.unlink()
            raise
        return StoredFile(
            key, digest, size, self.decided[0], sanitise_filename(self.filename), self.decided[1]
        )


async def _scan(sink: "_Sink", scanner: Any, required: bool) -> str | None:
    """Scan the received (plaintext) temp file before it is stored."""
    if scanner is None:
        return None
    from .av import ScanUnavailable

    assert sink.tmp_path is not None
    try:
        verdict, sig = await asyncio.to_thread(scanner.scan_file, sink.tmp_path)
    except ScanUnavailable:
        if required:
            raise UploadError(
                503, "evidence_scan_unavailable",
                "The malware scanner is not available; try again later.",
            ) from None  # fmt: skip
        log.warning("malware scanner unavailable; upload stored as not scanned")
        return "not_scanned"
    if verdict == "infected":
        log.warning("upload rejected by the malware scanner: %s", sig)
        raise UploadError(422, "evidence_rejected_malware",
                          "The file was rejected by the malware scanner.")  # fmt: skip
    return "clean"


async def receive_upload(
    chunks: AsyncIterator[bytes], content_type: str, storage: EvidenceStorage,
    max_image: int, max_audio: int, scanner: Any = None, scan_required: bool = False,
) -> StoredFile:  # fmt: skip
    """Parse a multipart body incrementally; exactly one part named ``file`` is accepted."""
    ctype, params = parse_options_header(content_type.encode("latin-1", "replace"))
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise UploadError(415, "unsupported_media_type", "Send the file as multipart/form-data.")

    sink = _Sink(storage, max_image, max_audio)
    state: dict[str, Any] = {"files": 0, "fields": 0, "in_file": False, "name": b"", "value": b""}
    headers: dict[bytes, bytes] = {}

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["name"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        headers[state["name"].lower()] = state["value"]
        state["name"], state["value"] = b"", b""

    def on_headers_finished() -> None:
        disp = headers.get(b"content-disposition", b"")
        _, opts = parse_options_header(disp)
        headers.clear()
        filename = opts.get(b"filename")
        if filename is None or opts.get(b"name") != b"file":
            raise UploadError(422, "unexpected_field", "Send exactly one file in the field 'file'.")
        state["files"] += 1
        if state["files"] > 1:
            raise UploadError(422, "one_file_per_request", "Send one file per request.")
        state["in_file"] = True
        sink.filename = filename.decode("utf-8", "replace")

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if state["in_file"]:
            sink.feed(data[start:end])

    def on_part_end() -> None:
        if state["in_file"]:
            sink.end_part()
            state["in_file"] = False

    parser = MultipartParser(
        boundary,
        callbacks={
            "on_header_field": on_header_field, "on_header_value": on_header_value,
            "on_header_end": on_header_end, "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data, "on_part_end": on_part_end,
        },
    )  # fmt: skip
    try:
        async for chunk in chunks:
            if chunk:
                await asyncio.to_thread(parser.write, chunk)
        await asyncio.to_thread(parser.finalize)
        if state["files"] != 1 or sink.decided is None:
            raise UploadError(422, "no_file", "Send one file in the field 'file'.")
        scan_status = await _scan(sink, scanner, scan_required)
        stored = await asyncio.to_thread(sink.commit)
        return dataclasses.replace(stored, scan_status=scan_status)
    except MultipartParseError as exc:
        sink.abort()
        raise UploadError(422, "malformed_multipart", "The upload body is malformed.") from exc
    except BaseException:
        sink.abort()
        raise

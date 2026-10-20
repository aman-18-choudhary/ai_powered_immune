"""Malware scanning of evidence uploads with a local ClamAV daemon (Phase 14).

The service speaks clamd's INSTREAM protocol over a Unix socket or TCP (no extra Python
package). Each upload is scanned before it is stored; the plaintext never leaves the machine.

* ``COMPLAINTS_CLAMD``: ``unix:/path/to/clamd.sock`` or ``host:port``. Unset: no scanning
  (evidence records ``scan: not_configured``).
* ``COMPLAINTS_AV_REQUIRED=1``: refuse uploads while the scanner cannot be reached (503).
  Otherwise such uploads are stored and marked ``not_scanned``.
* An infected file is always refused (422 ``evidence_rejected_malware``); the signature name is
  logged, nothing of the file.
"""

import logging
import socket
import struct
from pathlib import Path
from typing import Any

log = logging.getLogger("complaints.av")
CHUNK = 64 * 1024


class ScanUnavailable(Exception):
    pass


class ClamdScanner:
    def __init__(self, address: str, timeout_s: float = 30.0) -> None:
        self.address = address
        self.timeout_s = timeout_s

    def _connect(self) -> socket.socket:
        if self.address.startswith("unix:"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            target: Any = self.address[5:]
        else:
            host, _, port = self.address.rpartition(":")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target = (host or "127.0.0.1", int(port))
        s.settimeout(self.timeout_s)
        try:
            s.connect(target)
        except OSError:
            s.close()
            raise ScanUnavailable("clamd not reachable") from None
        return s

    @staticmethod
    def _reply(s: socket.socket) -> str:
        buf = b""
        while not buf.endswith(b"\x00"):
            part = s.recv(4096)
            if not part:
                break
            buf += part
        return buf.rstrip(b"\x00").decode("utf-8", "replace").strip()

    def ping(self) -> bool:
        try:
            with self._connect() as s:
                s.sendall(b"zPING\x00")
                return self._reply(s) == "PONG"
        except (ScanUnavailable, OSError):
            return False

    def version(self) -> str | None:
        try:
            with self._connect() as s:
                s.sendall(b"zVERSION\x00")
                return self._reply(s) or None
        except (ScanUnavailable, OSError):
            return None

    def scan_file(self, path: Path) -> tuple[str, str | None]:
        """("clean", None) or ("infected", signature). Raises ScanUnavailable."""
        try:
            with self._connect() as s, open(path, "rb") as f:
                s.sendall(b"zINSTREAM\x00")
                while chunk := f.read(CHUNK):
                    s.sendall(struct.pack(">I", len(chunk)) + chunk)
                s.sendall(struct.pack(">I", 0))
                reply = self._reply(s)
        except OSError:
            raise ScanUnavailable("scan failed") from None
        if reply.endswith("OK"):
            return "clean", None
        if reply.endswith("FOUND"):
            sig = reply.removeprefix("stream:").removesuffix("FOUND").strip()[:100]
            return "infected", sig
        raise ScanUnavailable("unexpected scanner reply")

    def status(self) -> dict[str, Any]:
        if not self.ping():
            return {"status": "unavailable", "reason": "clamd_not_reachable", "engine": "clamav",
                    "version": None}  # fmt: skip
        return {"status": "ready", "reason": None, "engine": "clamav", "version": self.version()}


class NoScanner:
    def status(self) -> dict[str, Any]:
        return {"status": "not_configured", "reason": "COMPLAINTS_CLAMD not set",
                "engine": None, "version": None}  # fmt: skip

    def scan_file(self, path: Path) -> tuple[str, str | None]:  # pragma: no cover - never called
        raise ScanUnavailable("not configured")

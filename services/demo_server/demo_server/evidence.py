"""Evidence endpoints over the real ledger app, and the offline verifier run.

``verify`` runs the ``verify.py`` that ships INSIDE the exported package, in a separate
interpreter (``-I -S``: no site packages, no environment-driven imports, no user site), with a
minimal environment, a wall-clock timeout and an output cap, pinned to the ledger's public key
with ``--trusted-pubkey``. The signing key never leaves the ``Signer``; only the public key is
passed on.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

log = logging.getLogger("demo_server.evidence")

MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_UNZIPPED_BYTES = 32 * 1024 * 1024
MAX_LINES = 40
MAX_LINE_CHARS = 300
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_REDACTED = re.compile(r"redacted .*?seqs \[([0-9, ]*)\]")
_CHAIN = re.compile(r"chain segment (\d+)\.\.(\d+) verified \((\d+) entries")
_CKPT = re.compile(r"(\d+) checkpoint")


class VerifyError(Exception):
    """The package could not be unpacked or verified (message is safe to show)."""


def sanitise(text: str) -> list[str]:
    out = []
    for ln in text.replace("\r", "").split("\n"):
        ln = _CTRL.sub("", ln).strip()
        if ln:
            out.append(ln[:MAX_LINE_CHARS])
        if len(out) >= MAX_LINES:
            break
    return out


def _unzip(data: bytes, dest: Path) -> None:
    if len(data) > MAX_PACKAGE_BYTES:
        raise VerifyError("package too large")
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = PurePosixPath(info.filename)
            if info.is_dir():
                continue
            if name.is_absolute() or ".." in name.parts or len(name.parts) != 1:
                raise VerifyError("unexpected member name in package")
            total += info.file_size
            if total > MAX_UNZIPPED_BYTES:
                raise VerifyError("package too large when unpacked")
            (dest / name.name).write_bytes(z.read(info))


def _status(code: int) -> str:
    return {0: "PASS_PINNED", 2: "INTEGRITY_OK_UNPINNED"}.get(code, "FAIL")


def parse(output: str, code: int) -> dict[str, Any]:
    lines = sanitise(output)
    redacted: list[int] = []
    entries = checkpoints = None
    for ln in lines:
        m = _REDACTED.search(ln)
        if m:
            redacted = [int(x) for x in m.group(1).replace(" ", "").split(",") if x]
        m = _CHAIN.search(ln)
        if m:
            entries = int(m.group(3))
        m = _CKPT.search(ln)
        if m and "chain segment" in ln:
            checkpoints = int(m.group(1))
    return {
        "exit_code": code, "status": _status(code), "lines": lines,
        "redacted_seqs": redacted, "entries": entries, "checkpoints": checkpoints,
    }  # fmt: skip


def _fail(line: str) -> dict[str, Any]:
    return {
        "exit_code": 1, "status": "FAIL", "lines": [line], "redacted_seqs": [],
        "entries": None, "checkpoints": None,
    }  # fmt: skip


async def run_verifier(
    package: bytes, public_key_hex: str, *, timeout_s: float = 20.0, output_cap: int = 64 * 1024,
    expected_verifier: bytes | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Unpack ``package`` and run its shipped ``verify.py``, pinned to ``public_key_hex``.

    Defence in depth: the package's ``verify.py`` must be byte-identical to the ledger's own
    verifier source (``expected_verifier``, default ``evidence_ledger.package.verifier_source()``)
    before it is executed, so a tampered package cannot bring its own "verifier"."""
    if expected_verifier is None:
        from evidence_ledger.package import verifier_source

        expected_verifier = verifier_source()
    tmp = await asyncio.to_thread(tempfile.mkdtemp, prefix="verify-")
    root = Path(tmp)
    proc: asyncio.subprocess.Process | None = None
    try:
        try:
            await asyncio.to_thread(_unzip, package, root)
        except zipfile.BadZipFile as e:
            raise VerifyError("package is not a valid zip file") from e
        script = root / "verify.py"
        if not script.is_file():
            raise VerifyError("package has no verify.py")
        if await asyncio.to_thread(script.read_bytes) != expected_verifier:
            return _fail("package verify.py differs from the ledger's verifier; not executed")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-S", str(script), str(root), "--trusted-pubkey", public_key_hex,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, cwd=str(root), env={"PATH": "/usr/bin:/bin"},
        )  # fmt: skip

        truncated = False

        async def read_capped() -> bytes:
            """Keep the first ``output_cap`` bytes; past the cap the verifier is killed and the
            rest is drained (not stored) so the pipe reaches EOF and the process is reaped."""
            nonlocal truncated
            assert proc is not None and proc.stdout is not None
            buf = bytearray()
            total = 0
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    return bytes(buf)
                total += len(chunk)
                if len(buf) < output_cap:
                    buf += chunk[: output_cap - len(buf)]
                if total > output_cap and not truncated:
                    truncated = True
                    proc.kill()

        try:
            out = await asyncio.wait_for(read_capped(), timeout_s)
            code = await asyncio.wait_for(proc.wait(), 5.0)
        except TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), 5.0)
            except TimeoutError:
                log.error("verifier process did not exit after kill")
            return _fail("verification timed out")
        if truncated:
            code = 1
        return parse(out.decode("utf-8", "replace"), int(code))
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        await asyncio.to_thread(shutil.rmtree, tmp, True)

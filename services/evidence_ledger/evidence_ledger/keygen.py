"""Dev/ops helper: ``python -m evidence_ledger.keygen [PATH]`` writes a new Ed25519 signing key
(PEM, mode 0600, refuses to overwrite) and prints only the path and the public key id."""

import os
import sys

from .keys import Signer


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = args[0] if args else "ledger_signing_key.pem"
    signer = Signer.generate()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(signer.private_pem())
    os.chmod(path, 0o600)
    print(
        f"wrote {path} (mode 0600) key_id={signer.key_id} public_key_hex={signer.public_raw.hex()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Deterministic, irreversible PII tokenizer."""

import hashlib
import hmac


class Tokenizer:
    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def token(self, value: str, kind: str) -> str:
        msg = f"{kind}:{value}".encode()
        return "tok_" + hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

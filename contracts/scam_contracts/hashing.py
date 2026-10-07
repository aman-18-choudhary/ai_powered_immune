import hashlib
import hmac


def keyed_hash(value: str, kind: str, federation_key: bytes) -> str:
    """HMAC-SHA256 hex of ``kind NUL value``; kind gives domain separation."""
    if not federation_key:
        raise ValueError("federation_key must not be empty")
    msg = f"{kind}\x00{value}".encode()
    return hmac.new(federation_key, msg, hashlib.sha256).hexdigest()

import hashlib
import hmac


def keyed_hash(value: str, kind: str, federation_key: bytes) -> str:
    """HMAC-SHA256 hex of ``kind:value``; kind gives domain separation."""
    msg = f"{kind}:{value}".encode()
    return hmac.new(federation_key, msg, hashlib.sha256).hexdigest()

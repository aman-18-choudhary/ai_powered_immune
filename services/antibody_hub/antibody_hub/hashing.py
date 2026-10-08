"""Re-export only: the keyed hash lives in ``scam_contracts.hashing`` (no local copy).

The hub never holds the federation key; this re-export exists for tests and tooling that play the
role of a bank.
"""

from scam_contracts.hashing import keyed_hash

__all__ = ["keyed_hash"]

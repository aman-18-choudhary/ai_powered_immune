"""The simulated world: citizens, banks, accounts. Raw ids live only here."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from scam_contracts.hashing import keyed_hash
from svckit.tokenizer import Tokenizer

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_START = datetime(2026, 1, 5, 0, 0, tzinfo=IST)

# (city, district, state, lat, lon, population weight)
CITIES: list[tuple[str, str, str, float, float, float]] = [
    ("Mumbai", "Mumbai", "Maharashtra", 19.076, 72.878, 12.0),
    ("Pune", "Pune", "Maharashtra", 18.520, 73.857, 5.0),
    ("Nagpur", "Nagpur", "Maharashtra", 21.146, 79.088, 2.5),
    ("Delhi", "New Delhi", "Delhi", 28.614, 77.209, 11.0),
    ("Gurugram", "Gurugram", "Haryana", 28.460, 77.027, 2.5),
    ("Bengaluru", "Bengaluru Urban", "Karnataka", 12.972, 77.594, 8.0),
    ("Chennai", "Chennai", "Tamil Nadu", 13.083, 80.271, 5.5),
    ("Coimbatore", "Coimbatore", "Tamil Nadu", 11.017, 76.956, 2.0),
    ("Hyderabad", "Hyderabad", "Telangana", 17.385, 78.487, 7.0),
    ("Kolkata", "Kolkata", "West Bengal", 22.573, 88.364, 6.0),
    ("Ahmedabad", "Ahmedabad", "Gujarat", 23.023, 72.572, 4.5),
    ("Surat", "Surat", "Gujarat", 21.170, 72.831, 3.0),
    ("Jaipur", "Jaipur", "Rajasthan", 26.912, 75.787, 3.5),
    ("Lucknow", "Lucknow", "Uttar Pradesh", 26.847, 80.947, 3.5),
    ("Kanpur", "Kanpur Nagar", "Uttar Pradesh", 26.450, 80.332, 2.0),
    ("Noida", "Gautam Buddha Nagar", "Uttar Pradesh", 28.535, 77.391, 2.5),
    ("Patna", "Patna", "Bihar", 25.594, 85.138, 2.5),
    ("Bhopal", "Bhopal", "Madhya Pradesh", 23.259, 77.413, 2.0),
    ("Indore", "Indore", "Madhya Pradesh", 22.720, 75.857, 2.5),
    ("Kochi", "Ernakulam", "Kerala", 9.931, 76.267, 2.0),
    ("Chandigarh", "Chandigarh", "Chandigarh", 30.734, 76.779, 1.5),
    ("Bhubaneswar", "Khordha", "Odisha", 20.296, 85.825, 1.5),
    ("Guwahati", "Kamrup Metropolitan", "Assam", 26.145, 91.736, 1.5),
]
# Cash-out hot spots (real-world mule/cash-out corridors) in addition to the metros.
CASHOUT_CITIES: list[tuple[str, float, float]] = [
    ("Jamtara", 23.963, 86.803),
    ("Mewat (Nuh)", 28.104, 77.001),
    ("Bharatpur", 27.217, 77.490),
    ("Deoghar", 24.485, 86.695),
    ("Delhi", 28.614, 77.209),
    ("Mumbai", 19.076, 72.878),
    ("Kolkata", 22.573, 88.364),
    ("Bengaluru", 12.972, 77.594),
    ("Hyderabad", 17.385, 78.487),
    ("Jaipur", 26.912, 75.787),
]
BANK_NAMES = ["sbi", "hdfc", "icici", "axis", "kotak", "pnb", "bob", "canara"]


@dataclass(frozen=True)
class Bank:
    bank_id: str


@dataclass(frozen=True)
class Account:
    account_id: str
    bank_id: str
    holder_id: str
    opened_at: datetime
    kind: str  # citizen | merchant | mule | cashout


@dataclass(frozen=True)
class Citizen:
    citizen_id: str
    state: str
    district: str
    city: str
    lat: float
    lon: float
    bank_id: str
    account_id: str
    device_id: str
    activity: float  # relative propensity to transact


@dataclass
class World:
    seed: int
    start: datetime
    banks: list[Bank]
    citizens: list[Citizen]
    merchants: list[Account]
    accounts: dict[str, Account]
    token_secret: bytes = b"sim-token-secret"
    federation_key: bytes = b"sim-federation-key"
    _tok: Tokenizer = field(init=False, repr=False)
    _hash_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _payer_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _dev_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tok = Tokenizer(self.token_secret)

    # Event-safe identifiers: only these ever leave the World.
    def payer_token(self, holder_id: str) -> str:
        if holder_id not in self._payer_cache:
            self._payer_cache[holder_id] = self._tok.token(holder_id, "payer")
        return self._payer_cache[holder_id]

    def device_token(self, device_id: str) -> str:
        if device_id not in self._dev_cache:
            self._dev_cache[device_id] = self._tok.token(device_id, "device")
        return self._dev_cache[device_id]

    def payee_hash(self, account_id: str) -> str:
        if account_id not in self._hash_cache:
            self._hash_cache[account_id] = keyed_hash(account_id, "payee", self.federation_key)
        return self._hash_cache[account_id]

    def phone_hash(self, number: str) -> str:
        return keyed_hash(number, "phone", self.federation_key)

    def citizen_by_token(self, token: str) -> Citizen:
        return next(c for c in self.citizens if self.payer_token(c.citizen_id) == token)


def stable_id(prefix: str, *parts: object, n: int = 16) -> str:
    raw = ":".join(str(p) for p in parts).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:n]}"


def build_world(
    seed: int, n_citizens: int, n_banks: int = 4, start: datetime | None = None
) -> World:
    rng = np.random.default_rng([seed, 1])
    start = start or DEFAULT_START
    banks = [Bank(f"bank_{BANK_NAMES[i] if i < len(BANK_NAMES) else i}") for i in range(n_banks)]
    w = np.array([c[5] for c in CITIES])
    city_idx = rng.choice(len(CITIES), size=n_citizens, p=w / w.sum())
    bank_idx = rng.choice(n_banks, size=n_citizens, p=_bank_shares(n_banks))
    age_days = rng.integers(200, 4000, size=n_citizens)
    young = rng.random(n_citizens) < 0.02  # a few legitimately new accounts
    age_days = np.where(young, rng.integers(3, 30, size=n_citizens), age_days)
    activity = rng.gamma(shape=1.5, scale=1 / 1.5, size=n_citizens)

    accounts: dict[str, Account] = {}
    citizens: list[Citizen] = []
    for i in range(n_citizens):
        city, district, state, lat, lon, _ = CITIES[int(city_idx[i])]
        cid = stable_id("cit", seed, i)
        acc_id = stable_id("acct", seed, "c", i, n=12)
        bank = banks[int(bank_idx[i])].bank_id
        accounts[acc_id] = Account(
            acc_id, bank, cid, start - timedelta(days=int(age_days[i])), "citizen"
        )
        citizens.append(
            Citizen(
                cid, state, district, city,
                float(lat + rng.normal(0, 0.05)), float(lon + rng.normal(0, 0.05)),
                bank, acc_id, stable_id("dev", seed, i), float(activity[i]),
            )
        )  # fmt: skip
    n_merch = max(60, n_citizens // 8)
    mage = rng.integers(100, 3000, size=n_merch)
    merchants = []
    for j in range(n_merch):
        acc_id = stable_id("acct", seed, "m", j, n=12)
        merchants.append(
            Account(
                acc_id, banks[j % n_banks].bank_id, stable_id("mer", seed, j),
                start - timedelta(days=int(mage[j])), "merchant",
            )
        )  # fmt: skip
        accounts[acc_id] = merchants[-1]
    return World(seed, start, banks, citizens, merchants, accounts)


def _bank_shares(n: int) -> np.ndarray:
    p = np.array([0.35, 0.25, 0.2, 0.1, 0.05, 0.03, 0.01, 0.01])[:n]
    if n > 8:
        p = np.concatenate([p, np.full(n - 8, 0.01)])
    return p / p.sum()

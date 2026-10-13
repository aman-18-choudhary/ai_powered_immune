"""Deterministic SYNTHETIC complaint dataset generator (development and testing only).

    python -m complaints.ml.synth [--out services/complaints/data/complaints_synthetic_v1.csv]

Every row is generated from hand-authored templates (``synth_templates.py``) by slot filling. No
real complaint, person, number or account was used or learned from. Anything measured on this data
describes the generator, not real-world performance.

Design points that the tests pin:

* ``group_id`` is the scenario family. The train/validation/test split is done by family: a
  template family that is in the test split contributes no template, and no row, to training.
* Augmentation (typos, casing, transliteration variants) is applied while generating, to a
  fraction of rows, and the augmented copy inherits ``group_id``/``split`` of its parent, so an
  augmented variant can never sit on the other side of the split from its source.
* Structured fields (phone, URL, UPI id, amount, payment method, city/state, social platform and
  account, email) are independently missing with category-specific probabilities. Missing is the
  generator's default; absence carries no label by itself beyond those documented rates.
* ``label`` is the observed label. About ``LABEL_NOISE`` of base rows are flipped to a plausible
  confusable category (``label_flipped=true``; ``true_category`` keeps the generator's label).
* The ``synthetic`` column is ``true`` on every row.
"""

import argparse
import csv
import random
import re
from pathlib import Path
from typing import Any

from .synth_templates import FAMILIES

SEED = 20261007
SYNTH_VERSION = "complaints_synthetic_v1"
LABEL_NOISE = 0.03
AUGMENT_FRACTION = 0.12
ROWS_PER_FAMILY = 36
ROWS_PER_FAMILY_OTHER = 40
CATEGORIES = tuple(FAMILIES)
FRAUD_CATEGORIES = tuple(c for c in CATEGORIES if c != "OTHER")
LANG_WEIGHTS = {"en": 0.50, "hi": 0.32, "dv": 0.18}  # dv only where a family has Devanagari
SPLITS = ("train", "validation", "test")
FAMILIES_PER_SPLIT = (6, 2, 2)  # of 10 families per category: 60/20/20

COLUMNS = [
    "row_id", "text", "language", "label", "true_category", "label_flipped", "template_id",
    "group_id", "split", "augmented", "parent_id", "hard_negative", "suspect_phone",
    "suspicious_url", "upi_id", "amount", "payment_method", "city", "state", "social_platform",
    "social_account", "suspect_email", "synthetic",
]  # fmt: skip

# probability that the optional field is present, per category
FIELD_P: dict[str, dict[str, float]] = {
    #                    phone  url    upi    amount pay   city   plat   acct   email
    "DIGITAL_ARREST":    dict(phone=.85, url=.05, upi=.15, amount=.70, pay=.45, city=.55, plat=.05, acct=.02, email=.05),  # noqa: E501
    "UPI_FRAUD":         dict(phone=.50, url=.15, upi=.75, amount=.90, pay=.90, city=.60, plat=.03, acct=.02, email=.02),  # noqa: E501
    "PHISHING":          dict(phone=.35, url=.75, upi=.05, amount=.35, pay=.25, city=.45, plat=.03, acct=.01, email=.20),  # noqa: E501
    "OTP_SCAM":          dict(phone=.80, url=.10, upi=.10, amount=.65, pay=.40, city=.55, plat=.03, acct=.01, email=.03),  # noqa: E501
    "BANK_FRAUD":        dict(phone=.60, url=.15, upi=.20, amount=.85, pay=.60, city=.60, plat=.02, acct=.01, email=.05),  # noqa: E501
    "INVESTMENT_SCAM":   dict(phone=.50, url=.40, upi=.60, amount=.90, pay=.65, city=.50, plat=.25, acct=.10, email=.05),  # noqa: E501
    "JOB_SCAM":          dict(phone=.55, url=.30, upi=.50, amount=.80, pay=.60, city=.55, plat=.20, acct=.08, email=.15),  # noqa: E501
    "SOCIAL_MEDIA_FRAUD": dict(phone=.15, url=.25, upi=.35, amount=.55, pay=.40, city=.40, plat=.80, acct=.60, email=.03),  # noqa: E501
    "OTHER":             dict(phone=.20, url=.15, upi=.05, amount=.35, pay=.20, city=.50, plat=.02, acct=.01, email=.05),  # noqa: E501
}  # fmt: skip

CONFUSABLE: dict[str, list[str]] = {
    "UPI_FRAUD": ["BANK_FRAUD", "OTP_SCAM"],
    "BANK_FRAUD": ["UPI_FRAUD", "OTP_SCAM"],
    "PHISHING": ["OTP_SCAM", "BANK_FRAUD"],
    "OTP_SCAM": ["PHISHING", "BANK_FRAUD"],
    "INVESTMENT_SCAM": ["JOB_SCAM", "SOCIAL_MEDIA_FRAUD"],
    "JOB_SCAM": ["INVESTMENT_SCAM", "SOCIAL_MEDIA_FRAUD"],
    "SOCIAL_MEDIA_FRAUD": ["INVESTMENT_SCAM", "JOB_SCAM"],
    "DIGITAL_ARREST": ["OTP_SCAM", "BANK_FRAUD"],
    "OTHER": ["BANK_FRAUD", "OTHER"],
}

BANKS = ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank", "Punjab National Bank", "Bank of Baroda",
         "Kotak Mahindra Bank", "Canara Bank", "Union Bank"]  # fmt: skip
APPS = ["PhonePe", "Google Pay", "Paytm", "BHIM", "Amazon Pay"]
AGENCIES = ["CBI", "Mumbai Police Cyber Cell", "Delhi Police", "Narcotics Control Bureau", "ED",
            "Customs department", "Crime Branch"]  # fmt: skip
COURIERS = ["FedEx", "Blue Dart", "DHL", "India Post", "Delhivery", "DTDC"]
PLATFORMS = ["Instagram", "Facebook", "WhatsApp", "Telegram", "Snapchat", "YouTube", "LinkedIn"]
CITIES = [("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"), ("Delhi", "Delhi"),
          ("Bengaluru", "Karnataka"), ("Hyderabad", "Telangana"), ("Chennai", "Tamil Nadu"),
          ("Kolkata", "West Bengal"), ("Jaipur", "Rajasthan"), ("Lucknow", "Uttar Pradesh"),
          ("Ahmedabad", "Gujarat"), ("Patna", "Bihar"), ("Indore", "Madhya Pradesh"),
          ("Nagpur", "Maharashtra"), ("Surat", "Gujarat"), ("Bhopal", "Madhya Pradesh")]  # fmt: skip
AMOUNTS = [499, 999, 1500, 2999, 4800, 9500, 12000, 25000, 48500, 75000, 150000, 320000]
HANDLES = ["okaxis", "oksbi", "okhdfcbank", "ybl", "paytm", "upi", "ibl", "axl"]
TLDS_BAD = ["xyz", "top", "click", "info", "online", "site", "cc"]
BRAND_TOKENS = ["sbi", "hdfc", "icici", "axis", "paytm", "phonepe", "gpay", "incometax", "uidai"]
SHORTENERS = ["bit.ly", "tinyurl.com", "cutt.ly", "rb.gy", "t.co"]
GENERIC_OPEN = {
    "en": ["", "", "", "Hello, ", "Sir, ", "Respected sir or madam, ", "Please help. "],
    "hi": ["", "", "", "Namaste, ", "Sir, ", "Kripya madad karein. "],
    "dv": ["", "", "", "नमस्ते, ", "सर, "],
}
GENERIC_CLOSE = {
    "en": ["", "", "", " Kindly take action.", " I am very worried.", " Please look into this.",
           " This happened yesterday."],  # fmt: skip
    "hi": ["", "", "", " Kripya karyavahi karein.", " Main bahut pareshan hoon.",
           " Ye kal hua tha."],  # fmt: skip
    "dv": ["", "", "", " कृपया कार्रवाई करें।", " मैं बहुत परेशान हूँ।"],
}
HINGLISH_VARIANTS = {
    "nahi": ["nhi", "nahin"], "hai": ["h", "hai"], "aapka": ["apka"], "kiya": ["kia"],
    "paise": ["paisa", "paise"], "mera": ["mera", "meraa"], "bola": ["bolaa", "bol diya"],
    "kripya": ["plz", "please"], "aaya": ["aya"], "karo": ["kro"], "wapas": ["vapas"],
    "jawab": ["javab"], "paisa": ["paise"], "bhejo": ["bhejo", "send karo"],
}  # fmt: skip


# ----------------------------------------------------------------------------- noise / augmentation
def perturb(text: str, rng: random.Random, strength: float = 0.08) -> str:
    """Typos, casing and transliteration variants. Deterministic for a given ``rng`` state."""
    out = []
    for word in text.split(" "):
        w = word
        low = w.casefold().strip(".,?!")
        if low in HINGLISH_VARIANTS and rng.random() < 0.5:
            w = rng.choice(HINGLISH_VARIANTS[low])
        elif len(w) > 4 and w.isascii() and rng.random() < strength:
            i = rng.randrange(1, len(w) - 2)
            op = rng.choice(("swap", "drop", "dup"))
            if op == "swap":
                w = w[:i] + w[i + 1] + w[i] + w[i + 2 :]
            elif op == "drop":
                w = w[:i] + w[i + 1 :]
            else:
                w = w[:i] + w[i] + w[i:]
        out.append(w)
    t = " ".join(out)
    r = rng.random()
    if r < 0.25:
        t = t.lower()
    elif r < 0.33:
        t = t.upper()
    elif r < 0.45:
        t = re.sub(r"[.,;]", "", t)
    return t


# ----------------------------------------------------------------------------- slot values
def _amount_text(rng: random.Random, n: int) -> str:
    style = rng.choice(("Rs {n:,}", "Rs.{n}", "₹{n:,}", "{n} rupees", "INR {n}"))
    return style.format(n=n)


def _upi(rng: random.Random, valid: bool = True) -> str:
    name = rng.choice(["rahul", "priya", "support", "kyc", "pay", "shop", "care", "vikas", "anita"])
    name += str(rng.randrange(10, 9999))
    handle = rng.choice(HANDLES) if valid else rng.choice(["fakebank", "pay", "x"])
    return f"{name}@{handle}"


def _phone(rng: random.Random) -> str:
    if rng.random() < 0.04:
        return rng.choice(["0000000000", "1234567890", "9999999999", "+910000000000"])
    n = rng.choice("6789") + "".join(rng.choice("0123456789") for _ in range(9))
    return rng.choice((n, "+91" + n, "+91 " + n[:5] + " " + n[5:], "0" + n))


def _url(rng: random.Random, kind: str | None = None) -> str:
    kind = kind or rng.choice(("lookalike", "lookalike", "lookalike", "short", "ip", "plain"))
    tail = "".join(rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(rng.randrange(4, 8)))
    if kind == "lookalike":
        b = rng.choice(BRAND_TOKENS)
        return f"https://{b}-{rng.choice(['kyc', 'secure', 'update', 'login'])}-{tail}." + rng.choice(
            TLDS_BAD
        )
    if kind == "short":
        return f"https://{rng.choice(SHORTENERS)}/{tail}"
    if kind == "ip":
        return "http://" + ".".join(str(rng.randrange(11, 250)) for _ in range(4)) + f"/{tail}"
    return f"http://{tail}-offers.com/claim?id={rng.randrange(1000, 99999)}"


def _fill(rng: random.Random) -> dict[str, Any]:
    n = rng.choice(AMOUNTS)
    city, state = rng.choice(CITIES)
    return {
        "bank": rng.choice(BANKS), "app": rng.choice(APPS), "agency": rng.choice(AGENCIES),
        "courier": rng.choice(COURIERS), "platform": rng.choice(PLATFORMS), "city": city,
        "state": state, "amount_n": n, "amt": _amount_text(rng, n), "days": rng.randrange(2, 15),
        "pct": rng.choice((15, 20, 25, 30, 40)), "acct": f"{rng.randrange(0, 10000):04d}",
        "url": _url(rng), "upi": _upi(rng),
    }  # fmt: skip


# ----------------------------------------------------------------------------- generation
def _split_assignment(category: str, keys: list[str], dev_keys: set[str], seed: int) -> dict:
    """Seeded family permutation; re-drawn until every split has Devanagari families."""
    rng = random.Random(f"{seed}:split:{category}")
    n_tr, n_va, _ = FAMILIES_PER_SPLIT
    for _ in range(200):
        perm = keys[:]
        rng.shuffle(perm)
        parts = (perm[:n_tr], perm[n_tr : n_tr + n_va], perm[n_tr + n_va :])
        d = [sum(k in dev_keys for k in p) for p in parts]
        if d[0] >= 2 and d[1] >= 1 and d[2] >= 1:
            return {k: s for s, p in zip(SPLITS, parts, strict=True) for k in p}
    raise RuntimeError("no split assignment found")  # pragma: no cover


def generate(seed: int = SEED) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        fams = FAMILIES[cat]
        keys = [f[0] for f in fams]
        dev = {f[0] for f in fams if f[1]["dv"]}
        assign = _split_assignment(cat, keys, dev, seed)
        p = FIELD_P[cat]
        per = ROWS_PER_FAMILY_OTHER if cat == "OTHER" else ROWS_PER_FAMILY
        for key, tmpls, hard in fams:
            rng = random.Random(f"{seed}:{cat}:{key}")
            langs = [lg for lg in ("en", "hi", "dv") if tmpls[lg]]
            weights = [LANG_WEIGHTS[lg] for lg in langs]
            for i in range(per):
                lang = rng.choices(langs, weights)[0]
                ti = rng.randrange(len(tmpls[lang]))
                slots = _fill(rng)
                body = tmpls[lang][ti].format_map(slots)
                text = rng.choice(GENERIC_OPEN[lang]) + body + rng.choice(GENERIC_CLOSE[lang])
                row: dict[str, Any] = {c: "" for c in COLUMNS}
                row.update(
                    row_id=f"r{len(rows):05d}", text=text, language=lang, true_category=cat,
                    template_id=f"{key}.{lang}{ti}", group_id=f"{cat}.{key}",
                    split=assign[key], augmented="false", parent_id="",
                    hard_negative="true" if hard else "false", synthetic="true",
                )  # fmt: skip
                # observed label (noise)
                label = cat
                if rng.random() < LABEL_NOISE:
                    label = rng.choice(CONFUSABLE[cat])
                row["label"] = label
                row["label_flipped"] = "true" if label != cat else "false"
                # structured fields, independently missing
                if rng.random() < p["phone"]:
                    row["suspect_phone"] = _phone(rng)
                if rng.random() < p["url"]:
                    in_text = "{url}" in tmpls[lang][ti]
                    row["suspicious_url"] = slots["url"] if in_text else _url(rng)
                if rng.random() < p["upi"]:
                    in_text = "{upi}" in tmpls[lang][ti]
                    row["upi_id"] = slots["upi"] if in_text else _upi(rng, rng.random() > 0.05)
                if rng.random() < p["amount"]:
                    row["amount"] = str(slots["amount_n"])
                if rng.random() < p["pay"]:
                    row["payment_method"] = rng.choice(
                        ["UPI", "UPI", "CARD", "NETBANKING", "WALLET", "BANK_TRANSFER", "OTHER"]
                        if cat != "JOB_SCAM"
                        else ["UPI", "BANK_TRANSFER", "NETBANKING", "OTHER"]
                    )
                if rng.random() < p["city"]:
                    row["city"] = slots["city"]
                    if rng.random() < 0.9:
                        row["state"] = slots["state"]
                elif rng.random() < 0.05:
                    row["state"] = slots["state"]
                if rng.random() < p["plat"]:
                    row["social_platform"] = slots["platform"]
                    if rng.random() < p["acct"] / max(p["plat"], 0.01):
                        row["social_account"] = "@" + rng.choice(["real", "the", "official", "mr"]) + (
                            "_" + str(rng.randrange(100, 9999))
                        )
                if rng.random() < p["email"]:
                    row["suspect_email"] = f"{rng.choice(['hr', 'support', 'care'])}{rng.randrange(10, 99)}@" + rng.choice(  # noqa: E501
                        ["mail-secure.top", "gmail.com", "jobs-portal.xyz", "outlook.com"]
                    )
                rows.append(row)
                # augmentation: copy inherits group/split, never crosses it
                if rng.random() < AUGMENT_FRACTION:
                    aug = dict(row)
                    aug.update(
                        row_id=f"r{len(rows):05d}", text=perturb(text, rng), augmented="true",
                        parent_id=row["row_id"], template_id=row["template_id"],
                    )  # fmt: skip
                    rows.append(aug)
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / f"{SYNTH_VERSION}.csv"

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    data = generate(a.seed)
    write_csv(data, a.out)
    print(f"wrote {len(data)} synthetic rows to {a.out} ({a.out.stat().st_size} bytes)")

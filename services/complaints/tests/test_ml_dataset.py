"""Synthetic dataset: determinism, group split without template leakage, balance, missingness,
label noise, near-duplicate guard, labelling."""

import collections
import re
from pathlib import Path

import pytest

from complaints.ml import synth

ROWS = synth.read_csv(synth.DEFAULT_CSV)
CATS = set(synth.CATEGORIES)


def _norm(t: str) -> str:
    t = re.sub(r"\d+", "#", t.casefold())
    return re.sub(r"[^\w#]+", " ", t).strip()


def test_generation_is_deterministic_and_matches_committed_csv(tmp_path: Path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    synth.write_csv(synth.generate(), a)
    synth.write_csv(synth.generate(), b)
    assert a.read_bytes() == b.read_bytes() == synth.DEFAULT_CSV.read_bytes()
    assert synth.generate(seed=1) != synth.generate()


def test_size_labelling_and_columns():
    assert len(ROWS) >= 3000
    assert synth.DEFAULT_CSV.stat().st_size <= 2 * 1024 * 1024
    assert set(ROWS[0]) >= {"text", "label", "group_id", "template_id", "split", "synthetic"}
    assert {r["synthetic"] for r in ROWS} == {"true"}
    assert all(r["text"].strip() for r in ROWS)
    assert {r["label"] for r in ROWS} == CATS
    assert {r["language"] for r in ROWS} == {"en", "hi", "dv"}


def test_group_split_has_zero_template_and_family_overlap():
    by = collections.defaultdict(set)
    tby = collections.defaultdict(set)
    for r in ROWS:
        by[r["split"]].add(r["group_id"])
        tby[r["split"]].add(r["template_id"])
    s = ("train", "validation", "test")
    for i, a in enumerate(s):
        for b in s[i + 1 :]:
            assert not by[a] & by[b], (a, b)
            assert not tby[a] & tby[b], (a, b)
    # every category has families in every split
    for cat in CATS:
        for sp in s:
            assert any(g.startswith(cat + ".") for g in by[sp]), (cat, sp)
    n = collections.Counter(r["split"] for r in ROWS)
    assert 0.5 < n["train"] / len(ROWS) < 0.7 and 0.12 < n["test"] / len(ROWS) < 0.28


def test_augmented_copy_stays_with_parent_split_and_group():
    byid = {r["row_id"]: r for r in ROWS}
    aug = [r for r in ROWS if r["augmented"] == "true"]
    assert 0.05 < len(aug) / len(ROWS) < 0.2
    for r in aug:
        p = byid[r["parent_id"]]
        assert (r["split"], r["group_id"], r["template_id"]) == (
            p["split"], p["group_id"], p["template_id"],
        )  # fmt: skip


def test_class_balance_and_languages_per_split():
    c = collections.Counter(r["label"] for r in ROWS)
    assert max(c.values()) / min(c.values()) < 1.5
    for sp in ("train", "validation", "test"):
        langs = collections.Counter(r["language"] for r in ROWS if r["split"] == sp)
        assert langs["dv"] >= 40 and langs["hi"] >= 150 and langs["en"] >= 300, (sp, langs)
        assert langs["dv"] / sum(langs.values()) < 0.3  # Devanagari is a modest share


def test_missingness_rates_per_category_are_category_specific():
    def rate(cat, col):
        rs = [r for r in ROWS if r["true_category"] == cat]
        return sum(bool(r[col]) for r in rs) / len(rs)

    assert rate("PHISHING", "suspicious_url") > 0.6
    assert rate("OTP_SCAM", "suspect_phone") > 0.65
    assert rate("INVESTMENT_SCAM", "upi_id") > 0.45 and rate("INVESTMENT_SCAM", "amount") > 0.8
    assert rate("SOCIAL_MEDIA_FRAUD", "social_platform") > 0.65
    assert rate("JOB_SCAM", "amount") > 0.65 and rate("JOB_SCAM", "payment_method") > 0.45
    assert rate("OTHER", "suspect_phone") < 0.35
    # every optional column is missing for a real share of rows (independent missingness)
    for col in ("suspect_phone", "suspicious_url", "upi_id", "amount", "city"):
        m = sum(not r[col] for r in ROWS) / len(ROWS)
        assert 0.15 < m < 0.95, col
    # text is always present while structured fields are not
    assert all(r["text"] for r in ROWS)
    assert any(not r["suspect_phone"] and not r["suspicious_url"] and not r["amount"] for r in ROWS)


def test_label_noise_rate_is_small_and_documented():
    base = [r for r in ROWS if r["augmented"] == "false"]
    flipped = sum(r["label_flipped"] == "true" for r in base) / len(base)
    assert 0.015 < flipped < 0.05
    assert all((r["label"] != r["true_category"]) == (r["label_flipped"] == "true") for r in ROWS)
    assert abs(synth.LABEL_NOISE - 0.03) < 1e-9


def test_hard_negatives_are_in_other_with_fraud_vocabulary():
    hn = [r for r in ROWS if r["hard_negative"] == "true"]
    assert len(hn) >= 60 and {r["true_category"] for r in hn} == {"OTHER"}
    assert any("otp" in r["text"].casefold() for r in hn)
    assert any("debited" in r["text"].casefold() for r in hn)


def test_no_exact_or_near_duplicate_leakage_between_train_and_test():
    train = {_norm(r["text"]) for r in ROWS if r["split"] == "train"}
    test = [_norm(r["text"]) for r in ROWS if r["split"] == "test"]
    assert not train & set(test)
    tr_sets = [set(t.split()) for t in train]
    near = 0
    for t in test:
        s = set(t.split())
        for o in tr_sets:
            if len(s & o) / len(s | o) >= 0.8:
                near += 1
                break
    assert near / len(test) < 0.02  # documented threshold: Jaccard >= 0.8 on word sets


def test_dataset_has_no_real_looking_account_numbers():
    # amounts are plain numbers; phones come from a documented random generator
    assert all(re.fullmatch(r"\d+", r["amount"]) for r in ROWS if r["amount"])


def test_hard_negative_families_are_in_every_split_and_train_has_several():
    by = collections.defaultdict(set)
    for r in ROWS:
        if r["hard_negative"] == "true":
            by[r["split"]].add(r["group_id"])
    assert len(by["train"]) >= 2 and len(by["validation"]) >= 1 and len(by["test"]) >= 1

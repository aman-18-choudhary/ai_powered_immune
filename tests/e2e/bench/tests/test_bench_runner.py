import re
from pathlib import Path

import pytest

from scam_bench.run_benchmark import (
    ABLATIONS,
    BenchConfig,
    default_report_path,
    render_report,
    resolve_ablations,
    run_benchmark,
)

TINY = BenchConfig(n_citizens=120, days=7, warmup_days=3, n_campaigns=2, victims_per_campaign=12)

SECTIONS = [
    "## Headline metrics",
    "## Operating point: step-up or hold",
    "## Per-rail breakdown",
    "## Per-role breakdown",
    "## Lead time before mass victimisation",
    "## Ablations",
    "## Scoring latency",
    "## Reproducibility",
    "## Limitations",
]


@pytest.fixture(scope="module")
def tiny_result(tmp_path_factory):
    out = tmp_path_factory.mktemp("rep") / "report.md"
    res = run_benchmark(7, ["no_call_signal"], config=TINY, out=out)
    return res, out


def test_smoke_report_has_every_section(tiny_result):
    res, out = tiny_result
    text = out.read_text()
    for s in SECTIONS:
        assert s in text, s
    for needle in (
        "seed", "UPI", "IMPS", "NEFT", "victim_transfer", "mule_forward", "p50", "p99",
        "no_call_signal", "NEFT has no scam", "simulator",
    ):  # fmt: skip
        assert needle in text, needle
    assert res["model_versions"]["call_guard"] == "clf-v1"  # classifier loaded, not rules fallback
    assert res["model_versions"]["txn_guard"] == "gbm-v1"
    assert res["seed"] == 7 and res["ablations"] == ["no_call_signal"]
    base = res["variants"]["baseline"]
    assert base["metrics"].n_scam > 0 and base["metrics"].n_benign > 0
    assert set(res["variants"]) == {"baseline", "no_call_signal"}
    assert len(base["lead"]) == TINY.n_campaigns
    assert res["latency_ms"]["p50"] <= res["latency_ms"]["p99"]


def test_report_has_no_raw_identifiers_or_wallclock(tiny_result):
    _, out = tiny_result
    text = out.read_text()
    assert not re.search(r"\b(cit|acct|txn|dev|mer)_[0-9a-f]{12}", text)
    assert "Generated at" not in text  # opt-in only


def test_determinism_same_seed(tmp_path):
    a = run_benchmark(11, [], config=TINY, out=tmp_path / "a.md")
    b = run_benchmark(11, [], config=TINY, out=tmp_path / "b.md")
    assert a["variants"]["baseline"]["metrics"] == b["variants"]["baseline"]["metrics"]
    assert render_report(a, include_volatile=False) == render_report(b, include_volatile=False)
    c = run_benchmark(12, [], config=TINY, out=tmp_path / "c.md")
    assert render_report(c, include_volatile=False) != render_report(a, include_volatile=False)


def test_no_antibody_not_implemented(tmp_path):
    assert "no_antibody" in ABLATIONS
    with pytest.raises(
        NotImplementedError, match="antibody-hub not built yet; wired in a later task"
    ):
        run_benchmark(7, ["no_antibody"], config=TINY, out=tmp_path / "x.md")
    assert not (tmp_path / "x.md").exists()


def test_unknown_ablation_and_training_seed_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown ablation"):
        run_benchmark(7, ["bogus"], config=TINY, out=tmp_path / "x.md")
    with pytest.raises(ValueError, match="training"):
        run_benchmark(101, [], config=TINY, out=tmp_path / "x.md")


def test_no_call_signal_zeroes_call_feature():
    (transform,) = resolve_ablations(["no_call_signal"])
    assert transform({"active_call_risk": 0.9, "x": 1.0}) == {"active_call_risk": 0.0, "x": 1.0}


def test_default_out_is_repo_docs():
    assert (
        default_report_path()
        == Path(__file__).resolve().parents[4] / "docs" / "benchmark_report.md"
    )

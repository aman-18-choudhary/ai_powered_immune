import re
from pathlib import Path

import pytest

from scam_bench.metrics import lead_time_detail
from scam_bench.run_benchmark import (
    BenchConfig,
    _td,
    default_report_path,
    main,
    render_report,
    resolve_ablations,
    run_benchmark,
)

TINY = BenchConfig(n_citizens=120, days=7, warmup_days=3, n_campaigns=2, victims_per_campaign=12)

SECTIONS = [
    "## Headline metrics",
    "Worst-rail FPR",
    "Per-rail false-positive rate",
    "## Precision at realistic prevalence",
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
    res = run_benchmark(
        7, ["no_call_signal", "degraded_call_signal"], config=TINY, out=out, include_volatile=True
    )
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
    assert res["seed"] == 7 and res["ablations"] == ["no_call_signal", "degraded_call_signal"]
    base = res["variants"]["baseline"]
    assert base["metrics"].n_scam > 0 and base["metrics"].n_benign > 0
    assert set(res["variants"]) == {"baseline", "no_call_signal", "degraded_call_signal"}
    assert len(base["lead"]) == TINY.n_campaigns
    assert res["latency_ms"]["p50"] <= res["latency_ms"]["p99"]


def test_report_has_no_raw_identifiers_or_wallclock(tiny_result):
    _, out = tiny_result
    text = out.read_text()
    assert not re.search(r"\b(cit|acct|txn|dev|mer)_[0-9a-f]{12}", text)
    assert "Generated at" not in text  # opt-in only
    assert "machine-dependent" in text  # fixture opted in to the latency block


def test_determinism_same_seed(tmp_path):
    a = run_benchmark(11, [], config=TINY, out=tmp_path / "a.md")
    b = run_benchmark(11, [], config=TINY, out=tmp_path / "b.md")
    assert a["variants"]["baseline"]["metrics"] == b["variants"]["baseline"]["metrics"]
    assert render_report(a, include_volatile=False) == render_report(b, include_volatile=False)
    c = run_benchmark(12, [], config=TINY, out=tmp_path / "c.md")
    assert render_report(c, include_volatile=False) != render_report(a, include_volatile=False)


def test_no_antibody_reproduces_the_pre_antibody_baseline(tmp_path):
    with_stage = run_benchmark(7, ["no_antibody"], config=TINY, out="")
    pre = run_benchmark(7, [], config=TINY, out="", antibody_stage=False)  # stage globally off
    key = lambda ds: [(d.txn_id, d.decision, d.score) for d in ds]  # noqa: E731
    assert key(with_stage["decisions"]["no_antibody"]) == key(pre["decisions"]["baseline"])
    assert (
        with_stage["variants"]["no_antibody"]["metrics"] == pre["variants"]["baseline"]["metrics"]
    )
    assert with_stage["variants"]["no_antibody"]["lead"] == pre["variants"]["baseline"]["lead"]
    assert with_stage["antibody_stats"].keys() == {"baseline"}  # only the baseline has the stage


def test_antibody_run_is_deterministic_and_adds_no_benign_matches():
    a = run_benchmark(7, ["no_antibody"], config=TINY, out="")
    b = run_benchmark(7, ["no_antibody"], config=TINY, out="")
    assert render_report(a, include_volatile=False) == render_report(b, include_volatile=False)
    assert a["antibody_stats"] == b["antibody_stats"]
    st = a["antibody_stats"]["baseline"]
    assert st["published"] >= 1 and st["benign_matches"] == 0
    on, off = a["variants"]["baseline"]["metrics"], a["variants"]["no_antibody"]["metrics"]
    assert on.fpr == off.fpr and on.recall >= off.recall  # antibodies only add scam holds
    text = render_report(a, include_volatile=False)
    assert "## Antibody stage" in text and "ASSUMED analyst-confirmation" in text
    assert "NotImplementedError" not in text


def test_unknown_ablation_and_training_seed_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown ablation"):
        run_benchmark(7, ["bogus"], config=TINY, out=tmp_path / "x.md")
    with pytest.raises(ValueError, match="training"):
        run_benchmark(101, [], config=TINY, out=tmp_path / "x.md")


def test_no_call_signal_zeroes_call_feature():
    (transform,) = resolve_ablations(["no_call_signal"])
    assert transform.transform({"active_call_risk": 0.9, "x": 1.0}) == {
        "active_call_risk": 0.0,
        "x": 1.0,
    }


def test_default_out_is_repo_docs():
    assert (
        default_report_path()
        == Path(__file__).resolve().parents[4] / "docs" / "benchmark_report.md"
    )


def test_default_report_omits_latency_and_is_byte_identical_via_cli(tmp_path):
    argv = [
        "--seed",
        "11",
        "--citizens",
        "120",
        "--days",
        "7",
        "--campaigns",
        "2",
        "--victims",
        "12",
    ]
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    assert main([*argv, "--out", str(a)]) == 0
    assert main([*argv, "--out", str(b)]) == 0
    assert a.read_bytes() == b.read_bytes()
    text = a.read_text()
    assert "p50" not in text and "machine-dependent" not in text
    assert "degraded_call_signal" in text  # CLI default ablations


def test_cli_rejects_bad_ablations_cleanly(tmp_path, capsys):
    for name, msg in (("bogus", "unknown ablation"),):
        with pytest.raises(SystemExit) as e:
            main(["--ablations", name, "--out", str(tmp_path / "x.md")])
        assert e.value.code == 2
        assert msg in capsys.readouterr().err
    assert not (tmp_path / "x.md").exists()


def test_reasons_off_gives_identical_decisions():
    fast = run_benchmark(5, [], config=TINY, out="", with_reasons=False)
    full = run_benchmark(5, [], config=TINY, out="", with_reasons=True)
    key = lambda ds: [(d.txn_id, d.decision, d.score) for d in ds]  # noqa: E731
    assert key(fast["decisions"]["baseline"]) == key(full["decisions"]["baseline"])
    assert fast["variants"]["baseline"]["metrics"] == full["variants"]["baseline"]["metrics"]


def test_degraded_call_signal_is_deterministic_and_imperfect():
    r1 = run_benchmark(5, ["degraded_call_signal"], config=TINY, out="")
    r2 = run_benchmark(5, ["degraded_call_signal"], config=TINY, out="")
    d1 = [(d.txn_id, d.score) for d in r1["decisions"]["degraded_call_signal"]]
    assert d1 == [(d.txn_id, d.score) for d in r2["decisions"]["degraded_call_signal"]]
    assert r1["degraded_params"] == {"call_recall": 0.85, "spurious_rate": 0.015}


def test_lead_table_matches_lead_time_detail_end_to_end(tmp_path):
    res = run_benchmark(5, [], config=TINY, out=tmp_path / "r.md")
    truth, txns = res["truth"], res["txns"]
    ds = res["decisions"]["baseline"]
    text = (tmp_path / "r.md").read_text()
    assert res["variants"]["baseline"]["lead"]
    for row in res["variants"]["baseline"]["lead"]:
        hold = lead_time_detail(row.campaign_id, ds, truth)
        assert hold == row.lead_hold
        line = next(ln for ln in text.splitlines() if ln.startswith(f"| {row.campaign_id} | "))
        assert _td(hold.lead) in line
        alert = lead_time_detail(
            row.campaign_id, [*ds, *res["call_risks"]], truth, include_calls=True
        )
        assert _td(alert.lead) in line
    assert txns


def test_mule_history_is_public():
    from txn_guard.simdata import mule_history

    assert callable(mule_history)


def test_antibody_sensitivity_table_and_honest_limitations():
    res = run_benchmark(7, ["no_antibody"], config=TINY, out="", sensitivity=True)
    sens = res["sensitivity"]
    assert set(sens) == {"__delay_0", "__delay_300", "__delay_900", "__hold_gate"}

    def rec(v):
        return v["metrics"].by_role["victim_transfer"].recall

    assert rec(sens["__delay_0"]) >= rec(res["variants"]["baseline"]) >= rec(sens["__delay_900"])
    text = render_report(res, include_volatile=False)
    for needle in (
        "Sensitivity of the confirmation model", "label-gated, 0 s", "label-gated, 300 s",
        "label-gated, 900 s", "hold-gated (no labels)", "tautology", "3-6 mule accounts",
        "poisoning",
    ):  # fmt: skip
        assert needle in text, needle
    assert "__delay" not in text and "__hold_gate" not in text  # hidden variants never leak
    assert set(res["variants"]) == {"baseline", "no_antibody"}


def test_sensitivity_is_off_by_default_in_the_api():
    res = run_benchmark(7, ["no_antibody"], config=TINY, out="")
    assert res[
        "sensitivity"
    ] == {} and "Sensitivity of the confirmation model" not in render_report(
        res, include_volatile=False
    )

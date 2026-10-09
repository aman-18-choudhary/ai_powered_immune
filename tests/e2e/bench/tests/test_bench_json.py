import json

from scam_bench.run_benchmark import BenchConfig, main

TINY = BenchConfig(n_citizens=120, days=7, warmup_days=3, n_campaigns=2, victims_per_campaign=12)


def test_json_out_schema_and_determinism(tmp_path):
    argv = [
        "--seed", "7", "--ablations", "no_call_signal", "no_antibody", "--out", "",
        "--citizens", "120", "--days", "7", "--campaigns", "2", "--victims", "12",
        "--hero-seeds", "0", "--no-sensitivity",
    ]  # fmt: skip
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert main([*argv, "--json-out", str(a)]) == 0
    assert main([*argv, "--json-out", str(b)]) == 0
    assert a.read_bytes() == b.read_bytes()  # deterministic (no runtime/latency by default)
    d = json.loads(a.read_text())
    assert d["schema_version"] == 1 and d["simulated"] is True
    assert d["meta"]["seed"] == 7 and d["meta"]["runtime_s"] is None and d["latency_ms"] is None
    assert {"UPI", "IMPS", "NEFT"} <= set(d["per_rail"])
    assert set(d["per_role"]) == {"victim_transfer", "mule_forward"}
    assert {"baseline", "no_call_signal", "no_antibody"} <= set(d["ablations"])
    h = d["headline"]
    assert h["n_scam"] > 0 and set(h["recall"]) == {"value", "ci95"}
    assert d["limitations"] and all(isinstance(x, str) for x in d["limitations"])
    assert d["sensitivity"] == [] and d["hero"] is None
    assert isinstance(d["ablations"]["baseline"]["money_prevented_upper_bound_inr"], str)


def test_json_out_with_latency_and_sensitivity(tmp_path):
    p = tmp_path / "s.json"
    assert main([
        "--seed", "7", "--ablations", "no_antibody", "--out", "", "--citizens", "120",
        "--days", "7", "--campaigns", "2", "--victims", "12", "--hero-seeds", "1",
        "--include-latency", "--json-out", str(p),
    ]) == 0  # fmt: skip
    d = json.loads(p.read_text())
    assert d["meta"]["runtime_s"] is not None and d["latency_ms"]["n"] > 0
    assert d["hero"]["seeds"] == 1
    labels = [r["label"] for r in d["sensitivity"]]
    assert labels[0] == "no antibody" and any("hold-gated" in x for x in labels)

import json
from importlib import resources

from conftest import login


def test_summary_endpoint_schema(hero_client):
    c = hero_client
    r = c.get("/api/benchmark/summary", headers=login(c, "citizen"))
    assert r.status_code == 200
    d = r.json()
    assert d["schema_version"] == 1 and d["simulated"] is True
    assert set(d) >= {
        "meta", "config", "headline", "per_rail", "per_role", "ablations", "antibody", "calls",
        "sensitivity", "hero", "latency_ms", "limitations",
    }  # fmt: skip
    assert set(d["meta"]) >= {
        "seed",
        "git_commit",
        "model_versions",
        "forbidden_seeds",
        "runtime_s",
    }
    assert d["meta"]["seed"] == 42 and d["meta"]["runtime_s"] is not None
    h = d["headline"]
    for k in ("precision", "recall", "f1", "fpr", "flagged_recall"):
        assert set(h[k]) == {"value", "ci95"}
    assert h["recall"]["ci95"][0] <= h["recall"]["value"] <= h["recall"]["ci95"][1]
    assert {"UPI", "IMPS", "NEFT"} <= set(d["per_rail"])
    assert set(d["per_role"]) == {"victim_transfer", "mule_forward"}
    assert {"baseline", "no_antibody", "no_call_signal", "degraded_call_signal"} <= set(
        d["ablations"]
    )
    base, off = d["ablations"]["baseline"], d["ablations"]["no_antibody"]
    assert base["victim_transfer_recall"]["value"] >= off["victim_transfer_recall"]["value"]
    assert d["sensitivity"] and d["sensitivity"][0]["label"] == "no antibody"
    assert d["hero"]["seeds"] >= 1 and set(d["hero"]["with_antibody"]) == {
        "allow", "step_up", "hold_verify",
    }  # fmt: skip
    assert d["limitations"] and all(isinstance(x, str) and x for x in d["limitations"])
    assert isinstance(base["money_prevented_upper_bound_inr"], str)
    assert {"p50", "p99", "n"} == set(d["latency_ms"])


def test_bundled_file_is_valid_json_and_matches_endpoint(hero_client):
    raw = resources.files("demo_server").joinpath("data", "benchmark_summary.json").read_text()
    on_disk = json.loads(raw)
    c = hero_client
    assert c.get("/api/benchmark/summary", headers=login(c, "admin")).json() == on_disk

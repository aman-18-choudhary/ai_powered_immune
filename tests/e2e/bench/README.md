# scam-bench

In-process evaluation harness: simulator world -> call-guard -> txn-guard -> metrics.

```
python -m scam_bench.run_benchmark --seed 42 --out docs/benchmark_report.md
python -m scam_bench.run_benchmark --seed 42 --out "" --include-latency --json-out summary.json
```

## `--json-out PATH`: structured summary

Writes the machine-readable counterpart of the markdown report (`scam_bench.summary`). The file is
a pure function of seed, config and code: byte-identical across runs. Machine-dependent values
(`meta.runtime_s`, `latency_ms`) are `null` unless `--include-latency` is passed. Every rate is
`{"value": number|null, "ci95": [lo, hi]|null}` (Wilson 95%); money is a decimal string in INR.

| key | content |
|---|---|
| `schema_version` | integer, currently 1 |
| `simulated` | always `true`: all numbers come from simulator data |
| `meta` | `seed`, `git_commit`, `runtime_s`, `model_versions`, `forbidden_seeds` (seeds the models were fitted on; the run seed is checked to be disjoint), `command`, `ablations_requested` |
| `config` | world size: `n_citizens`, `days`, `warmup_days`, `n_campaigns`, `victims_per_campaign`, `benign_call_rate` |
| `headline` | baseline counts (`n_benign`, `n_scam`, `tp`, `fp`, `fn`, `tn`) and rates `precision`, `recall`, `f1`, `fpr`, `step_up_benign_rate`, `flagged_recall`, `flagged_fpr` |
| `per_rail` | `UPI` / `IMPS` / `NEFT` -> same shape as `headline` |
| `per_role` | `victim_transfer` / `mule_forward` -> `n`, `held`, `flagged`, `recall`, `flagged_recall` |
| `ablations` | variant name (`baseline`, `no_antibody`, `no_call_signal`, `degraded_call_signal`) -> headline fields plus `victim_transfer_recall`, `mule_forward_recall`, `mean_share_post_detection_held`, `money_prevented_upper_bound_inr`, `money_at_risk_after_first_hold_inr` |
| `antibody` | `confirm_delay_s`, per-variant `stats` (`published`, `matches`, `scam_matches`, `benign_matches`), `stage_enabled` |
| `calls` | call-guard session counts: `scam_calls`, `scam_calls_alerted`, `benign_calls`, `benign_calls_alerted`, `alerts_published` |
| `sensitivity` | list of confirmation-model rows (`label`, `gate` none/label/hold, `delay_s`, `victim_transfer_recall`, `mean_share_post_detection_held`, `antibodies_published`, `antibody_matches`, `benign_matches`); empty with `--no-sensitivity` |
| `hero` | `null` with `--hero-seeds 0`, else `seeds`, `model_only` and `with_antibody` decision counts for victim B's first transfer, `counts`, `mule_age_days_range` |
| `latency_ms` | `{p50, p99, n}` only with `--include-latency`, else `null` |
| `limitations` | list of strings, the report's Limitations section |

The simulator is the only data source; see `limitations`.

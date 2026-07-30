# A/B Grok harness: `baseline` vs `contract_strip`

**Generated**: 2026-07-30 20:23:35
**Model**: `grok-4.5`
**N**: 5 councils (ok A=5, ok B=5, rated=5)
**Arm A**: Production mirror: low effort, no contract, no strip
**Arm B**: Phase-0 package: contract + strip

## Verdict

**SHIP** Phase-0 change for arm B

| Gate | Pass |
|------|------|
| G1 reliability | yes |
| G2 quality non-inferior | yes |
| G3 quality or latency | yes |
| G4 cleanliness | yes |
| G5 no verbosity explosion | yes |

## Structural

| Metric | A | B |
|--------|---|---|
| error_rate | 0.0 | 0.0 |
| median_elapsed_s | 157.0 | 134.7 |
| mean_preamble_chars | 666.6 | 0.0 |
| toolish_start_rate | 1.0 | 0.0 |
| median_clean_chars | 15505.0 | 8734.0 |

## Quality (blind pairwise)

| win_rate(B) | 0.8 |
| delta_help (B−A) | 0.8 |


## Contract compliance

- contract_ok A: 0%
- contract_ok B: 100%
- has_verdict A/B: 100% / 100%

## Per-council

| council_id | A s | B s | preA | preB | toolA | toolB | win |
|------------|-----|-----|------|------|-------|-------|-----|
| 203545 | 165.76 | 96.11 | 948 | 0 | True | False | tie |
| 191803 | 136.88 | 117.24 | 495 | 0 | True | False | B |
| 191503 | 156.99 | 134.73 | 544 | 0 | True | False | tie |
| 133415 | 133.65 | 135.62 | 691 | 0 | True | False | B |
| 112921 | 169.44 | 150.04 | 655 | 0 | True | False | B |

## Next

- If SHIP: implement Phase-0 in `owlex/agents/grok.py` (contract suffix + strip in cleaner).
- If DO NOT SHIP: inspect failing gates; iterate arm design; do not change prod defaults.

See `docs/design/ab-grok-harness-benchmark.md`.

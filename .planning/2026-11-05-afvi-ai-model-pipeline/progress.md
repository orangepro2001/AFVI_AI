# Progress Log

## Session: 2026-11-05

### Current Status
- **Phase:** 3 - Implementation
- **Started:** 2026-11-05

### Actions Taken
- Resumed the existing AFVI_AI implementation in `/mnt/c/dev/AFVI_AI`; verified the local branch is at `72ec44b` and the Laravel bootstrap commit is already pushed.
- Confirmed `ssh AI` access and 374,325 lines in the existing JPG manifest.
- Resumed parallel work on the PxSpecTool Go exporter and conservative Python baseline; assigned a read-only server data coverage audit.
- Confirmed existing Python contract/manifest/LOT-time split suite passes all 18 tests.
- Built and ran the Go exporter on one real read-only group via PxSpecTool/result; recorded point labels, one-LOT limitation, timestamps, and manifest mapping coverage.
- Identified and assigned corrections for real LOT extraction, timezone preservation, and machine-prefixed manifest paths.
- Stopped a read-only whole-tree CIFS filename scan after >100 seconds; broad ResultData enumeration is too costly for repeated discovery and is not required for the one-group exploratory baseline.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `PYTHONPATH=python python3 -m unittest discover -s python/tests -v` | All existing data contract tests pass | 18 tests passed | PASS |
| `ssh AI 'cd ~/dev/AFVI_AI && go test -mod=mod ./cmd/afvi-export'` | Exporter tests pass against sibling PxSpecTool | Passed | PASS |
| `ssh AI 'cd ~/dev/PxSpecTool && go test ./result'` | Existing parser tests pass | Passed | PASS |
| Real group export + manifest join | Establish real sample support without modifying source data | 26,757 points; 26,022 VRS observed; 26,066/26,757 manifest entries | PASS with one-LOT limitation |

## Session: 2026-11-06

### Current Status
- **Phase:** Complete - stable baseline, audit, handoff, and synchronization
- **Started:** 2026-11-06

### Actions Taken
- Resumed from committed Go exporter milestone `85ee21d`; confirmed the AI server checkout is synchronized at that commit and the real export/manifest remain available outside Git.
- Ran the Python unit suite and compile check: 27 tests passed.
- Independent review found point-weighted fitting/metrics and class labels concentrated in too few LOTs could overstate evidence; implementation is being strengthened before producing a real-data report.
- Confirmed a dashboard schema mismatch: Python emits `data_source` as an object, while Blade initially rendered it as text. The dashboard worker corrected the normalization and fixture.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Python unit suite | Contract, join, split, baseline tests pass | 27 passed before the weighting changes | PASS |
| Python compile check | Baseline package compiles | Passed before the weighting changes | PASS |
| Laravel dashboard suite | Missing, invalid, and valid report states work in the AI Composer container | 5 tests, 17 assertions | PASS before the final report-view changes |

### Errors
| Error | Resolution |
|-------|------------|
| First remote exporter test attempt could not locate package because files were synced at repo root | Corrected the remote destination and reran the exporter tests successfully. |
| Default server Go build reported inconsistent vendor metadata | Re-ran using -mod=mod, which passed tests and built the exporter. |

### Handoff Closeout
- The stable baseline remains bccb57a; the latest report is one-LOT exploratory-only with no held-out metrics.
- Re-read the report: one training LOT, 26,022 labeled rows, and no test LOTs. All displayed scores are marked exploratory_in_sample.
- Read the existing 374,325-row JPG manifest and identified six explicit result groups without traversing the BMP tree. Exported them read-only through the PxSpecTool adapter to /home/tony/afvi-ai-data/exports-afvi13-20261106/all.jsonl (129,140 rows, about 74 MB).
- Four groups under physical LOT D07834026H3112 contain 88,410 rows; TEST3 and TEST4 have no VRS rewrites. The audit export was not used for training or performance claims.
- Added docs/handoff.md and updated task plan/findings to reflect the stable baseline, measured data support, and next evaluation gate.
- Final action: commit and push documentation, fast-forward the AI checkout to the exact same commit, and verify both tracked source worktrees are clean at the same SHA.

| Check | Result |
|-------|--------|
| Python unit suite at handoff | 30 passed |
| Go exporter tests on AI | go test -mod=mod ./cmd/afvi-export passed |
| Explicit manifest group export | 129,140 rows across six groups; only one labeled physical LOT |

## Session: 2026-11-06 (multi-LOT)

- Found about 116 candidate LOTs by bounded listing of ResultData/Data; added inventory and coverage tools, per-(LOT, state) reservoir sampling with weights, a fixed area-only reference with paired bootstrap, and a per-machine/model breakdown.
- Exported 185 production groups (11.88M points, 74 LOTs). Pre-registered the cutoff and criteria before scoring.
- Round 1 (frozen) was not qualified: 0.518. The user approved round 2 on the same holdout. CV-tuned structured model: held-out 0.655 (0.565-0.711), meeting the criteria with the caveats recorded in findings.
- Dashboard published by the user at https://tony-ai-pc-1.tail9f7f1f.ts.net/afvi_ai (tailscale serve path /afvi_ai -> 127.0.0.1:18000).

| Check | Result |
|-------|--------|
| Python unit suite | 42 passed |
| Laravel suite (composer:2, PHP 8.5) | 6 passed, 39 assertions; Pint passed |
| Round-2 report on dashboard via tailnet domain | AUC 0.655 and holdout history shown |

## Session: 2026-11-06 (usable outputs)

- Added a LOT-averaged review coverage curve (descriptive; no selection) and per-family recall at 30%. A rerun reproduced the round-2 metrics exactly. Held-out: top 30% finds 57.5% of keyed points (area-only 38.8%, random 30%).
- Added afvi_ai.confirm (frozen-model verdict on unseen LOTs) and afvi_ai.score (per-LOT review CSV with image path, saved-image check, and family evidence).
- Demo review lists in /home/tony/afvi-ai-data/review-demo/out. Python 51 tests and Laravel 6 tests passed.

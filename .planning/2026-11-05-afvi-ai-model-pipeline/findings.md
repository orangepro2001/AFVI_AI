# Findings & Decisions

## Current verified state (2026-11-06)

- Local /mnt/c/dev/AFVI_AI and AI ~/dev/AFVI_AI were both clean at bccb57a before this handoff. The configured GitHub remote is git@github.com:orangepro2001/AFVI_AI.git.
- The stable implementation has a Go exporter using sibling PxSpecTool/result, a Python LOT-balanced structured baseline, and a read-only Laravel dashboard. The baseline uses three structured features and strong L2 regularization; Python tests protect against a single large LOT dominating fit or metrics.
- The server report at /home/tony/afvi-ai-data/reports/latest.json covers 26,757 merge points from one LOT: 1,976 non-zero VRS labels, 24,046 low-confidence assumed-good labels, and 735 unlabeled. Status is insufficient_support; no validation metrics are emitted. All 2,000 listed sample scores use exploratory_in_sample.
- The existing AFVI13 JPG manifest contains 374,325 rows and six result groups: four beneath LOT D07834026H3112, and one each named TEST3 and TEST4 beneath model 25FBO015-15.
- A read-only export of those six explicit manifest-listed groups contains 129,140 rows (about 74 MB), stored outside Git at /home/tony/afvi-ai-data/exports-afvi13-20261106/all.jsonl. The four groups under D07834026H3112 contain 88,410 rows: 5,926 positive, 76,179 assumed-good, and 6,305 without VRS. TEST3 and TEST4 contribute 40,730 unlabeled rows and no VRS rewrites; their production status is unverified. The audit has not been used to produce a new model report.
- A broad recursive VRS filename scan over CIFS previously exceeded 100 seconds. Future inventory should use an explicit group manifest or a verified existing index; do not repeat the broad scan.
- Python 30-test suite passes locally. Go exporter tests pass on AI with go test -mod=mod ./cmd/afvi-export. The prior Composer run passed six Laravel tests with 33 assertions.
- Formal evaluation requires at least five independent LOTs per split, at least 20 rows of each class per split, and each class represented in at least three LOTs per split. Current data is far below the independent-LOT requirement.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Keep the model at the stable three-feature, strongly regularized baseline while independent LOT count is low | More capacity or repeated tuning on one held-out batch would make overfitting harder to detect. |
| Do not count TEST3 / TEST4 as supervised evidence | Their explicit exports have no VRS rewrites, and production LOT identity has not been established. |
| Do not report formal performance until class-by-LOT support gates pass in both temporal splits | Many points from one LOT do not represent independent production conditions. |
| Keep images as manifest references; do not ingest BMP trees or read pixels in this phase | Image ingestion is not needed for the structured baseline. |
| Keep VRS historical-label evaluation distinct from parameter optimization | The model cannot identify AFVI misses and replay equivalence has not been proven. |

## Remaining gate

Obtain or identify more independently labeled production LOTs. First audit their explicit group list and class coverage. Only then export the required groups, run the frozen evaluation policy, and review support and uncertainty. Parameter replay requires a separate same-frame reproducibility proof.

## Multi-LOT evaluation pre-registration (2026-11-06, fixed before any scoring)

- Inventory: bounded listing of ResultData/Data/{AFVI13,AFVI14} found 228 production groups after excluding TEST/DRY-RUN names. Export: 185 groups exported read-only; 39 groups lacked Bundle001.ini and DRY-RUN groups had no strip XML.
- Coverage (labels and timestamps only): 74 LOTs, 62 with VRS labels, 59 with positives; about 168k positives and 5.0M assumed-good points.
- Frozen split: cutoff 2026-08-15T00:00:00+09:00, test_fraction 0.2, seed 0. Before cutoff 48 LOTs (39 with positives); after cutoff 26 LOTs (20 with positives).
- Frozen model: structured-logistic-v2, three log1p features, L2 2.0, negative weight 0.25; sampling 1,500 rows per (LOT, label state), re-weighted to full totals.
- Qualified means: evaluation status evaluated and model status validated_candidate; held-out LOT-bootstrap ROC-AUC 95% lower bound > 0.5; held-out ROC-AUC above the fixed log1p(area_total) reference, reported with a paired LOT-bootstrap difference interval. The split and model are not changed after seeing held-out results.

## Multi-LOT evaluation result (2026-11-06) — not qualified

- Report: /home/tony/afvi-ai-data/reports/multilot-20261106.json (also latest.json). Input 11,878,219 rows from 185 groups; 266,461 kept by per-(LOT, state) sampling.
- Status evaluated / validated_candidate: 34 training LOTs, 28 held-out LOTs (positives in 32 / 27).
- Held-out ROC-AUC 0.518 (LOT bootstrap 95% 0.479–0.551); AP 0.505. Reference log1p(area_total) ROC-AUC 0.560; paired model−reference difference −0.105 to +0.023.
- Verdict against pre-registered criteria: not qualified (lower bound not above 0.5, does not beat the reference).
- Diagnosis: coefficients are near zero (area 0.0013, length −0.021, width −0.006). L2 strength 2.0 on a per-LOT-normalized mean loss was sized for one LOT and shrinks the model to a near-constant. This held-out set has now been looked at once.

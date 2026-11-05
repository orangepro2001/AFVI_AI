# AFVI AI Handoff

Updated: 2026-11-06

## Current state

A read-only research prototype ranks AFVI merge points that already exist in ResultData by historical VRS-rewrite risk. It does not detect points AFVI missed, write parameters, or recommend production settings.

The Go adapter (cmd/afvi-export) reads ResultData through the sibling PxSpecTool/result package. Python (stdlib only) inventories groups, audits label coverage, samples per (LOT, label state), and fits a LOT-balanced ridge logistic model. Laravel displays the external JSON report; the preview container binds 127.0.0.1:18000 and is published on the tailnet at https://tony-ai-pc-1.tail9f7f1f.ts.net/afvi_ai.

## Data

- Inventory of ResultData/Data/{AFVI13,AFVI14} (bounded to model/LOT/group): 228 production groups after excluding TEST and DRY-RUN names. 185 exported read-only to /home/tony/afvi-ai-data/exports-20261106-all; the training input is the symlink directory exports-20261106-production. 39 groups lacked Bundle001.ini and were skipped.
- 11,878,219 merge points, 74 LOTs, 62 with VRS labels, 59 with positives (about 168k positives, 5.0M assumed-good).
- The PxTemporary shares (.61/.62/.63) have no LOT identity and are not used. No BMP or JPG pixels were read.

## Evaluation (pre-registered in .planning findings)

Split: cutoff 2026-08-15T00:00:00+09:00 plus a seeded 20% of earlier LOTs; 34 training LOTs and 28 held-out LOTs. Qualified means: evaluated / validated_candidate, held-out LOT-bootstrap ROC-AUC lower bound above 0.5, and held-out ROC-AUC above the fixed log1p(area_total) reference.

- Round 1, frozen size3 features with L2 2.0: held-out ROC-AUC 0.518 (0.479–0.551). Not qualified; the L2 setting from the one-LOT era shrank the model to near-constant. Report: reports/multilot-20261106.json.
- Round 2, settings chosen by 5-fold LOT-grouped CV on training LOTs only (structured features, L2 0.001; CV mean 0.736): held-out ROC-AUC 0.655 (0.565–0.711), AP 0.613; reference 0.560; paired model minus reference difference −0.005 to +0.162. Meets the pre-registered criteria. Report: reports/multilot-tuned-20261106.json, also latest.json.

Caveats: round 2 is the second use of the same held-out LOTs (selection itself used only training LOTs). The advantage over area-only ranking is not significant at 95%. CV chose the smallest L2 at the grid edge. The held-out score is lower than CV (0.655 vs 0.736). Performance is strong on the 7M99, 24FBO060-19, 24MSP037-15 and 25FBO015-15 families (0.79–0.87) and near random on 4SP0223G01 (0.50) and 6ST2001Q01 (0.54).

## Next work

1. Confirm on production LOTs inspected after 2026-11-06: inventory and export only new groups, then score them with the round-2 settings unchanged. That is the first fully unseen test.
2. Investigate the weak B-series models (4SP0223G01, 6ST2001Q01) on training LOTs only. Possible causes are label semantics, missing fields, or the need for image information.
3. Keep image work and label assistance (for example multimodal review) as a separate, approved phase. It needs a decision on whether factory images may leave the network.

Run commands are in docs/modeling.md. Parameter replay remains a separate gate: it must reproduce the machine's output for the same frame, mask, spec, and parameters before parameter comparisons can be trusted.

## Verification at handoff

- Python unit suite: 42 tests passed.
- Laravel suite in the composer:2 container (PHP 8.5): 6 tests passed; Pint clean.
- Go exporter built with go build -mod=mod; the export of 185 groups is logged in exports-20261106-all/export.log.

## Source synchronization

This host (tony-ai-pc-1) is the AI server, and ~/dev/AFVI_AI is the working checkout. Every accepted milestone is committed and pushed to origin/main. Runtime data, .env, and installed dependencies stay outside Git.

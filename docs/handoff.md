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

## Frozen model and confirmation

The round-2 settings were refit on all 62 labeled LOTs and saved as /home/tony/afvi-ai-data/models/afvi-risk-v3-20261106.json via train --final-model. A rerun of the round-2 command reproduced the report exactly. python -m afvi_ai.confirm scores only LOTs absent from the artifact's training_lots, applies the same gates, and returns the pre-registered verdict. A smoke test on a seen LOT correctly refused a verdict. On 2026-11-06 a fresh inventory found no new groups (the newest data is from 2026-11-02), so confirmation waits for new production.

## Usable outputs

- Model: /home/tony/afvi-ai-data/models/afvi-risk-v3-20261106.json (structured, L2 0.001, refit on 62 labeled LOTs; metadata carries per-family held-out evidence).
- Review coverage on held-out LOTs, LOT-averaged share of keyed VRS points found when reviewing each LOT's top fraction by score: top 10% 27.5%, top 20% 43.1%, top 30% 57.5%, top 50% 74.8%. Area-only ranking gives 13.3% / 25.9% / 38.8% / 61.8%; random order gives the fraction itself. At the top 30%, the validated families reach 77–88% (7M99-535/586/593, 24FBO060-19, 24MSP037-15, 25FBO015-15); B-series reaches 25–44%.
- Review lists: python -m afvi_ai.score writes one ranked CSV per LOT plus summary.json. Demo output for RH6268036400- (7M99-535, part of the final model's training data) and MGP363046700E (SPFCE300N4.KMA0, a family the model never saw) is in /home/tony/afvi-ai-data/review-demo/out. Only about 1–2% of the top 500 points in these LOTs have a saved defect image; the inspection program does not save every image.
- Dashboard: https://tony-ai-pc-1.tail9f7f1f.ts.net/afvi_ai shows the evaluation, the review coverage table, and per-family results.

## Parameter draft

python -m afvi_ai.propose writes /home/tony/afvi-ai-data/proposals/afvi-min-defect-area-draft-20261106.xml, marked DRAFT_NOT_FOR_MACHINE_USE. It covers lower limits on area_max per family, side, light, channel and SR/metal: the limit is set on training LOTs within a 0.5% / 1% keyed-loss budget, then checked on held-out LOTs. Of 225 conditions, 6 are candidates (7M99-586 and 7M99-593 only), 8 exceed the budget on held-out LOTs, and 7 are unverified (7M99-460 has no held-out LOTs). Candidate review reductions are only 0–5.6%, so area lower limits are not the main lever against false calls. Observed minimum areas change between training and held-out LOTs in several conditions, which indicates the machine limits were edited between LOTs. The real InspectionSpec.xml files are not on the .203 share, and the mapping to spec keys 1024/1025 and 1002/1011 is unverified.

## Label findings

- ivs_1 is the operator's key-in code at the review station; "0" means nothing was keyed (PxSpecTool docs/result-data/50-ivs-keyin.md).
- ResultData/DLG lists the strips sent to review, not a deep-learning subsample. Inside reviewed strips, the keying rate is the same on and off the list (3.8% vs 4.4%).
- Code systems differ by family: 7M99 letter codes; FBO/MSP 3–4 digit codes; B-series 6-digit codes (450xxx, 850xxx, 880xxx, 150xxx). The code meanings are not in ResultData. A frequency table for the quality department is at /home/tony/afvi-ai-data/inventory/vrs-codes-by-family.csv.
- B-series families are weak in training-LOT CV as well (4SP0223G01 0.48, 6ST2001Q01 0.27), so the label definition for numeric code systems is the first suspect.

## Next work

1. When new production LOTs appear, export only the new groups and run afvi_ai.confirm with the frozen artifact. Do not refit or retune first.
2. Obtain the VRS code table (code to defect name or disposition) for the B-series and FBO/MSP families, and decide per code whether it means a real defect. Re-label from that decision before any new model round; any new round must be selected on training LOTs and confirmed on unseen LOTs.
3. Keep image work and label assistance (for example multimodal review) as a separate, approved phase. It needs a decision on whether factory images may leave the network.

Run commands are in docs/modeling.md. Parameter replay remains a separate gate: it must reproduce the machine's output for the same frame, mask, spec, and parameters before parameter comparisons can be trusted.

## Verification at handoff

- Python unit suite: 58 tests passed.
- Laravel suite in the composer:2 container (PHP 8.5): 6 tests passed (42 assertions); Pint clean.
- Go exporter built with go build -mod=mod; the export of 185 groups is logged in exports-20261106-all/export.log.

## Source synchronization

This host (tony-ai-pc-1) is the AI server, and ~/dev/AFVI_AI is the working checkout. Every accepted milestone is committed and pushed to origin/main. Runtime data, .env, and installed dependencies stay outside Git.

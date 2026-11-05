# AFVI AI Handoff

Updated: 2026-11-06

## Stable baseline

The latest stable source baseline is commit bccb57a on main (dashboard), following exporter 85ee21d and conservative Python baseline bd56e9b. This is a read-only research prototype for ranking AFVI merge points that already exist in ResultData. It does not detect points AFVI missed, write parameters, or recommend production settings.

The Go adapter reads ResultData through the existing PxSpecTool/result package. Python joins the existing JPG manifest and trains a low-capacity, LOT-balanced, regularized logistic model on three structured measurements. It does not enumerate BMP files or read image pixels. Laravel displays the external JSON report and binds to the AI server loopback address.

## Current evidence

The AI server is reachable with ssh AI. The current /home/tony/afvi-ai-data/reports/latest.json was produced from one result group and contains 26,757 merge points: 1,976 keyed non-zero VRS codes, 24,046 low-confidence assumed-good points, and 735 unlabeled points. The points belong to one physical LOT. Consequently the report is insufficient_support / exploratory_only, has no holdout metrics, and marks all 2,000 displayed scores as in-sample exploratory scores.

The existing JPG manifest lists 374,325 images across six groups: four groups under 25FBO015-15/D07834026H3112 and groups named TEST3 and TEST4. A read-only audit export of those groups contains 129,140 merge points (about 74 MB) in /home/tony/afvi-ai-data/exports-afvi13-20261106/all.jsonl. The four groups for the same physical LOT contain 88,410 points, including 5,926 keyed positives, 76,179 assumed-good points, and 6,305 unlabeled points. The two TEST-named groups contain 40,730 unlabeled points and no VRS rewrites. Their production identity is unverified, so they are not evidence for supervised validation. This audit export has not been used to generate a new model report.

Formal historical evaluation remains unavailable. The evaluator requires at least five independent LOTs in each split and each label class represented in at least three LOTs per split; larger point counts within one LOT do not satisfy this requirement. Even a passing report would describe historical VRS-label generalization only, not production recall or parameter improvement.

## Run and verify

From the AI server, the last report can be regenerated from the existing single-group export using the command in docs/modeling.md. Inputs are read-only; reports and exports stay under /home/tony/afvi-ai-data, outside Git. The web preview is loopback-only; use ssh -L 18000:127.0.0.1:18000 AI and open http://127.0.0.1:18000 when the preview container is running.

Verified at handoff:

- Python unit suite: 30 tests passed locally against the committed source.
- Go exporter tests: passed on AI with go test -mod=mod ./cmd/afvi-export.
- The report schema and support state were inspected on AI; report remains one-LOT exploratory-only.
- Six manifest-listed groups were exported read-only through the Go adapter. No BMP or JPG pixel data was read.
- AI server and local repository are to be left on the same pushed commit with clean source worktrees. Runtime data, .env, installed dependencies, and server-preserved Git stashes are host-local and are not source files to overwrite.

## Next work

Wait for more independently labeled production LOTs or a user-identified existing index of such LOTs. Then inventory only an explicit group list, verify VRS class coverage per physical LOT, and batch-export those groups outside Git. Do not repeat the broad CIFS walk; the previous recursive VRS filename scan exceeded 100 seconds. Before calling an evaluation formal, verify both split support gates and inspect performance by machine/model/material/side/light/channel where those fields are available.

Keep the model small and frozen while data is scarce. Do not add image models, broaden feature searches, or tune hyperparameters against the same held-out LOTs. Parameter replay remains a separate gate: it must reproduce the machine's output for the same frame, mask, spec, and parameters before parameter comparisons can be trusted.

## Source synchronization

Treat /mnt/c/dev/AFVI_AI and ~/dev/AFVI_AI on ssh AI as one source checkout. Every accepted milestone is committed and pushed to origin/main, then the AI checkout is advanced to that exact commit. Verify git rev-parse HEAD, branch, and git status --short on both sides after each sync. GitHub SSH authentication is unavailable from AI, so transfer pushed commits with a local Git bundle when needed. Preserve the server's existing stashes and host-specific runtime files.

# Task Plan: AFVI AI model pipeline

## Goal
Stabilize and document the read-only AFVI point-risk prototype, verify its limits against the AI server's actual indexed data, and leave local and AI source checkouts synchronized.

## Next Step
[Confirm the round-2 model on production LOTs inspected after 2026-11-06 with settings unchanged; investigate weak B-series models on training LOTs only.]

## Current Phase
Complete: multi-LOT evaluation; round 2 meets pre-registered criteria (held-out ROC-AUC 0.655, 0.565-0.711)

## Phases

### Phase 1: Requirements & Discovery
- [x] Confirm scope, read-only production boundary, label semantics, and no-BMP-ingestion constraint
- [x] Confirm the AI server and existing JPG manifest availability
- [x] Record prior implementation and tests
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Keep Go export adapter on PxSpecTool/result and Python as the only modeling runtime
- [x] Define LOT/time-isolated validation and fail-closed small-sample reporting
- [x] Keep runtime data and artifacts outside Git
- **Status:** complete

### Phase 3: Implementation
- [x] Verify real-data Go JSONL export against the sibling PxSpecTool checkout
- [x] Implement conservative structured-feature baseline and uncertainty-aware evaluation
- [x] Add local, read-only Laravel coverage/evaluation dashboard and worker entry points
- [x] Configure loopback deployment and external data paths
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Run Go and Python tests, Laravel tests in the AI Composer container, and sample export checks
- [x] Verify data paths remain read-only and the web server binds to loopback
- **Status:** complete

### Phase 5: Delivery
- [x] Commit and push each verified milestone to origin/main
- [x] Synchronize accepted source changes to ~/dev/AFVI_AI on AI
- [x] Explain training readiness and limits from observed data coverage
- [x] Write a handoff that records the stable baseline and source synchronization procedure
- **Status:** complete

## Stable Baseline
- Source baseline before this handoff: bccb57a (main), with exporter 85ee21d and LOT-balanced baseline bd56e9b in history.
- Existing report: one physical LOT, 26,757 points, no held-out LOT metrics; explicitly exploratory-only.
- Manifest-listed audit: six groups, 129,140 exported points; only four groups / 88,410 points belong to the same labeled physical LOT. TEST3 and TEST4 have no VRS rewrite and are not established as production LOTs.
- The audit export is external data and was not used to claim or produce a new evaluation report.
- Handoff: docs/handoff.md.

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Exclude no-VRS and conflicting merge points from supervised fitting | They do not have a reliable observed binary label. |
| Treat keyed zero/empty values as low-confidence assumed good and report sensitivity | The field assumption is useful but can introduce label noise, especially in small data. |
| Use only compact, regularized structured features for the first baseline | Small datasets cannot support high-capacity models or broad feature searches reliably. |
| Do not publish a performance claim when group/sample/class minima are not met | Prevents a tiny or unrepresentative holdout from appearing authoritative. |
| Require explicit LOT from the export record | The real ResultData shape is model/LOT/group/strip; deriving LOT from the first group path segment would leak across lots. |
| Equalize each LOT's total contribution during feature scaling and fitting | Tens of thousands of points from one LOT are not tens of thousands of independent production examples. |
| Require each label class in at least three independent LOTs per split | A class represented by only one LOT can produce an apparently strong but batch-specific score. |
| Compute primary metrics with equal class-by-LOT weight and bootstrap LOT clusters | Prevent large LOTs from dominating model evaluation and uncertainty estimates. |
| In an evaluated report, show scores only for held-out LOTs; label fallback scores exploratory/in-sample | Keep the dashboard from presenting training predictions as independent validation. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Default Go build mode rejected server vendor metadata as inconsistent with go.mod | Used the repository's verified -mod=mod mode; exporter tests and build then passed. |

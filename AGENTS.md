# AFVI AI repository rules

- Keep this application independent from PxSpecWeb and production services. Never write to machine parameters, production databases, or shared ResultData/JPG inputs.
- Parse ResultData XML through the existing PxSpecTool result package. Do not add a second XML parser.
- Use only VRS rewrite files (`*_IVS.xml`) as observed labels. Preserve the original `ivs_1` string. A keyed non-zero code is positive; an unkeyed point is only an assumed negative with lower confidence. A missing VRS rewrite is unlabeled.
- Keep the natural key at merge-point granularity: machine, result group, strip, side, unit index, and image merge index. Do not silently collapse duplicate keys or conflicting VRS codes.
- Read the existing JPG manifest and derive image paths from its `rel` field. Never enumerate or convert the large BMP tree for model ingestion.
- Keep datasets, image caches, SQLite runtime databases, logs, reports, and model artifacts outside Git, under the configured AFVI data directory.
- The web service must bind to loopback. Do not expose it publicly or add parameter writes.
- Report historical VRS generalization only. Do not claim production recall, parameter optimization, or detection of defects AFVI missed.
- The Go adapter uses the sibling `../PxSpecTool` source on the AI server. Changes to that repository are outside this project's scope.
- Run Laravel commands in the Composer container on `ssh AI`; run Python unit tests before committing data-pipeline changes.
- Commit and push each small, reviewable milestone to the configured Git remote, as the user explicitly authorized.

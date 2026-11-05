# AFVI AI

AFVI AI is an internal, read-only research application for ranking already detected AFVI defect points for human review. It does not write machine parameters, and VRS labels cannot reveal defects that AFVI never detected.

The first baseline uses structured features parsed from ResultData and treats `ivs_1` as the label source. A non-zero VRS code is a positive label; an unkeyed point is only an assumed negative with lower confidence. Reports describe generalization to historical VRS decisions, not production recall or validated parameter improvements.

Laravel provides the local web interface. Python handles dataset joins, grouped evaluation, and later scoring. Result XML parsing is delegated to the existing `PxSpecTool/result` Go package. JPG references are joined from the existing manifest; the pipeline does not scan or convert BMP files.

## Data boundaries

- ResultData and JPG roots are configured outside this repository and are read-only inputs.
- Training tables, logs, reports, caches, and model files belong under `/srv/afvi-ai-data` (or another external data directory).
- The web service binds to `127.0.0.1`; access it through an SSH tunnel.
- Parameter replay and parameter recommendations remain disabled until same-frame, same-parameter replay reproduces the observed device result.

## Development

The runtime is hosted on `ssh AI`. Use the server's Composer container for Laravel commands and the system Python/Go runtimes for the data pipeline. Keep the local checkout and `~/dev/AFVI_AI` synchronized through the Git remote.

```sh
composer install
php artisan test
python -m unittest discover -s python/tests
```

See `docs/` for the data contract, evaluation protocol, and server setup.

# Operations and troubleshooting

## Start and inspect

```sh
python tools/dev.py setup
python tools/dev.py demo --approve-demo
python tools/dev.py check
python tools/dev.py serve
```

The demo uses `data/demo`; the API defaults to `data/platform`. To inspect demo
state through the API, set `AEROINTEL_HOME=data/demo` in the shell before serving.
`.env.example` is documentation; Python CLI does not silently load it. Compose
uses its usual environment interpolation. Keep real `.env` files untracked.

```sh
uv run --no-sync aerointel --home data/demo status RUN_ID
uv run --no-sync aerointel --home data/demo resume RUN_ID --attempts 3
uv run --no-sync aerointel --home data/demo investigate REPORT_ID
uv run --no-sync mlflow ui --backend-store-uri sqlite:///data/demo/objects/experiments/mlflow.db
```

Resume continues the failed/current stage, reusing epoch checkpoints and artifacts.
A live worker's file lock prevents takeover even if its lease is stale. After an
actual crash, wait up to the lease duration (30 seconds), then resume. Do not delete
the lock file or mutate pipeline tables to force concurrency. Non-transient schema,
policy and data errors require correction/new dataset version or run configuration.

## Ingest real data and approve

Create a complete dataset manifest using `specs/models/dataset-manifest.schema.json`.
Verify terms and source-scene IDs; set CRS/bounds null when unknown. Relative image
paths are resolved only under the chosen source root. Misaligned TIFFs must be
registered/aligned to a north-up grid before ingestion; never just overwrite
metadata to make them fit. Rotated/sheared georeferenced TIFFs are rejected.

```sh
uv run --no-sync aerointel ingest manifest.json --source-root PATH_TO_DATA
uv run --no-sync aerointel run DATASET_ID:VERSION --epochs 12
uv run --no-sync python tools/create_identity.py approver --id reviewer
```

Store generated identity configuration and tokens securely in the shell, then:

```sh
uv run --no-sync aerointel approve MODEL_ID:VERSION --confirm-human-approval
```

Approval uses `AEROINTEL_TOKEN` and the server-owned identity map. Switch to a
configured operator credential for `aerointel deploy MODEL_ID:VERSION`. Do not
weaken gates for a failed candidate. Missing spatial evidence blocks promotion.

## Canary failure and rollback

An unhealthy replay records a failed deployment and preserves the old active
pointer. Inspect deployment history, report integrity and artifact loading before
retrying with a new request ID. The API `/deployments/rollback` requires an operator
and activates the prior model only after rechecking approval, policy and health.
AWS rollback is governed by alarms and asynchronous endpoint state; see `aws.md`.

## Drift and feedback

Input drift opens an investigation. Inspect sample counts, source changes,
regional coverage and reference selection. Missing labels are not evidence of
degradation. Submit correctly shaped binary masks against prediction IDs, then
rerun monitoring. Propose retraining only with a matching actionable drift report.

## Environment issues

- Rasterio uses its bundled PROJ database. An inherited PostgreSQL/QGIS PROJ path
  previously broke GeoTIFF decoding; application initialization now isolates it.
- Use `tools/dev.py setup` to retain the compatible locked pandas/Evidently set.
  Do not upgrade pandas to 3.x independently of the monitoring compatibility test.
- If local sandbox restrictions hang FastAPI/TestClient or Terraform plugin IPC,
  run checks in a normal local shell. No AWS permissions are needed for tests.
- If Docker's Linux engine is absent, start Docker Desktop and rerun Compose build,
  health and demo checks. Compose config validation alone is not a container test.
- MLflow artifact URIs are absolute. View host-native runs on the host; create
  container-native runs under the shared container mount. Relocation needs an
  explicit artifact URI migration; do not edit registry artifact hashes.

## Backup and cleanup

Stop workers/API before copying the complete platform home. Preserve SQLite,
objects, MLflow database/artifacts, reports and approvals together. Verify hashes
after restore and recheck the active model before serving. SQLite WAL mode requires
a consistent snapshot; never copy only `metadata.db` while writes are active.

`docker compose down` stops local containers and retains data. Invalid uploads may
leave unreferenced content-addressed objects; these are harmless and no garbage
collector deletes evidence automatically. Remove synthetic demo directories only
after stopping workers and deciding no evidence needs retaining. Cloud cleanup
requires a reviewed destroy plan and explicit approval as documented in `aws.md`.

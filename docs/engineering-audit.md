# Engineering audit and handover — 2026-11-02

## Decision

AeroIntel now implements a working, persisted **local v1 MLOps lifecycle** with
real CPU model training and synthetic end-to-end evidence. It is not production
certified and is not a demonstrated live AWS deployment. Real imagery results,
Docker startup and cloud staging remain explicit verification gaps.

## What existed and what changed

The initial repository had ten JSON schemas, policy/ADR documents, a health API,
CLI contract validator, conservative gate evaluator, package/config/CI skeleton
and 38 tests. Those 38 tests were rerun successfully before implementation.
There was no actual dataset ingestion, trained model, pipeline persistence,
registry, inference service, monitoring backend, MCP transport or AWS infrastructure.

Implemented:

- Content-addressed snapshots, license/metadata/image validation, TIFF alignment,
  checksums, duplicate IDs/pairs, version immutability and atomic completion events.
- Leased/locked SQLite pipeline state machine, deterministic preprocessing/splits,
  independent experiments, epoch checkpoints, persisted failures/resume, bounded
  transient retry, logical-run deduplication and transactional outbox.
- Small independently implemented Siamese U-Net, binary masks/probabilities,
  honest mask metrics, validation-selected checkpoints, MLflow params/metrics/
  artifacts/lineage, entropy review heuristic and optional GeoJSON output.
- Scene/grid/region/overlap grouping, random-versus-independent-holdout comparisons,
  report/gate enforcement, missing-geography rejection and region metrics.
- Immutable model records, human-bound approval, report/split/dataset/artifact/
  policy integrity, replay canary, pointer CAS, history, rollback and health checks.
- Prediction provenance/idempotency, immutable label feedback, input/regional shift,
  actual labeled degradation, Evidently JSON/HTML and evidence-backed proposals.
- Authenticated API and CLI writes, six-tool official stdio MCP, read-only default,
  backend role/evidence/approval checks, raw argument validation and denied-write
  audit. Deterministic incident investigation requires no LLM key.
- Narrow S3/EventBridge/SageMaker Pipeline/Model Registry/endpoint/runtime/CloudWatch
  adapters; mocked AWS tests; Terraform storage/event/DLQ/roles/package group and
  optional bounded worker definition; explicit cloud plan/apply/cleanup helpers.
- CPU-only lockfile, setup/demo/check drivers, Docker targets/Compose, CI,
  OpenTelemetry seams, operational/security/data/credential documentation.

## Design decisions

Preserved the modular monolith and existing versioned schemas. Separate immutable
model content, human approval and active deployment; recheck all bindings before
activation. The final registered artifact belongs to the spatial experiment;
scene/random results describe separately trained comparison experiments, not
three scores fabricated from one training run. Thresholds are host-configured,
validated and content-digest-bound. Missing spatial evidence never passes.

Local canary is a held-out replay/health preflight; native AWS canary uses the
SageMaker blue/green capacity rollout. The optional AWS Pipeline runs the exact
local engine inside one bounded CPU Processing job, with approval/deployment
remaining separate. This is a deliberate small-v1 adapter, not a claim of a
separate scalable SageMaker Training-step DAG. See ADR 005.

## Verification record

The final `python tools/dev.py check` transcript is retained locally at
`.cache/final-complete-check.log`. The command exited successfully: **115 passed,
15 dependency/fixture warnings in 187.18 seconds**. No coverage percentage or
live-cloud pass is claimed.

| Command / check | Evidence |
| --- | --- |
| Initial `python -m pytest -q` | 38 passed before implementation |
| `python tools/dev.py setup` | Locked environment resolved/audited successfully on Python 3.12.11 |
| `python -m app.cli.main --home data/final-demo demo --approve-demo` | Completed → approved → active → inferred → labeled monitoring → shift → proposal |
| `python tools/dev.py check` | Exit 0; 115 tests passed; Ruff/formatting/mypy passed; sdist/wheel/resource/config/Compose/Terraform checks passed |
| `mypy` | No issues in 65 source files |
| `uv build` / `tools/verify_package.py` | aerointel-1.0.0 sdist and wheel built; ten schemas and policies verified |
| Installed-wheel contract smoke test | Passed using the installed wheel in an isolated target directory |
| `python -m compileall -q app modules adapters contracts` | Passed |
| Real Uvicorn process on 127.0.0.1:8765 | HTTP health returned status=ok and contract_version=1.0.0; exact process stopped |
| Checkov 3.4.2 | 58 passed, 0 failed, 11 documented demo exceptions, 0 parsing errors |
| `docker info` | Failed: Docker Desktop Linux engine pipe unavailable |
| GitHub Actions execution | Configured, not run on GitHub |
| Terraform apply / actual AWS resources | Never executed or created |

Machine-readable evidence: [final checks](results/verification.json),
[synthetic run](results/synthetic-demo.json) and
[static infrastructure security](results/infrastructure-security.json).

Critical tests cover failed spatial gates despite excellent random scores;
duplicate events/concurrent submissions; no promotion from training success;
human identity/role requirements; corrupt artifacts and changed policies; failed
canary retaining old active model; rollback; missing-label null performance;
immutable registry/audit; interrupted stages/dead-worker recovery; exact checkpoint
equivalence; upload/publish failure injection; real HTTP/MCP stdio validation and
denial audit; cloud package integrity, strict SDK request contracts and retry safety.

## Failures found and corrected

The do-loop exposed actual defects: evaluated class annotations shadowing `list`,
an incompatible pandas/Evidently dependency pair, inherited PostgreSQL PROJ data,
overlong Windows atomic/object filenames, duplicate canary activation races,
incomplete AWS stub shapes, missing cloud artifact-digest binding, an invalid
Terraform display name, absent dead-letter delivery and incomplete-upload cleanup.
Those were corrected and affected checks rerun. Old failed transcripts are retained
in the local cache, not presented as final passes.

Remaining test warnings originate in dependency deprecations, constant-column
correlation calculations in Evidently, and the intentionally corrupted TIFF fixture.
They are not silently turned into measured degradation. The decision engine uses
finite scalar distribution distances and requires real labels for performance.

## Exact remaining production gaps

1. No real LEVIR-CD+ dataset/license verification, real aerial benchmark, calibrated
   uncertainty, meaningful production operating threshold or geography result.
   Synthetic IoU is software evidence, not aerial-model quality evidence.
2. Docker daemon unavailable. Container builds, startup, volume permissions,
   native Linux serving and memory/load behavior remain unverified here.
3. AWS checks use Moto/stubs and static Terraform. Account-specific IAM, service
   quotas, ECR images, job execution, endpoint serving, native canary alarms,
   rollback, teardown and actual cost measurement require an authorized staging run.
4. Eleven explicit static-security exceptions remain appropriate only to the demo.
   Resolve encryption-key/retention/network/signing/access-log/replication policy
   before production; `docs/aws.md` explains scope and cost implications.
5. SQLite/file locks target one host and local disk. There is no distributed worker
   scheduler, multi-host lease guarantee, external immutable audit archive,
   backup/restore certification or disaster-recovery exercise.
6. Static local bearer identities require production OIDC/MFA/expiry/rotation,
   TLS/rate limiting and formal approval identity assurance before network exposure.
7. Datasets/tiles are intentionally small and memory-resident. No multi-tenant
   isolation, large-raster stress/load benchmark, GPU determinism guarantee,
   antimeridian support or automatic image registration is claimed. Rotated/sheared
   TIFFs are explicitly rejected to avoid incorrect bounds-only GeoJSON transforms.
8. MLflow artifact locations are absolute and may require migration when moving
   a platform home between hosts/containers. Checkpoints resume at epoch boundaries;
   crash-retried tracking entries may repeat while authoritative state remains unique.
9. Repo has no selected public license. No third-party reference code or data was
   copied; owner selection is needed before public redistribution as a licensed project.

## User inputs

Nothing secret or paid is required for the local demo/tests. For real data, supply
lawfully usable imagery/masks, source-scene IDs and reliable geographic metadata.
For optional cloud work, review account/region, SSO profile, scoped deployment and
execution roles, bucket/ECR/image choices, service quotas, rollback alarms, budget
and teardown. Do not paste secrets into chat. The exact requirement/permission/
configuration matrix is in `docs/credentials.md`; cost resource estimates and
manual approval boundaries are in `docs/aws.md`.

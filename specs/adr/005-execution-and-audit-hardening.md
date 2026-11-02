# ADR 005: Durable workflow, conservative promotion, minimal cloud worker

Status: Accepted, implementation audit 2026-11-02.

The foundation contained no trained model, registry, queue, or security backend.
Keep its schema wire format and build those domain behaviors behind one composition
root (`Platform`). `home` now owns SQLite, immutable objects, experiment tracking
and run output; remove unused configuration paths that implied separate stores.
Training seeds belong to versioned TrainingConfig/checkpoints, not a second
unused global setting. CLI home selection honors environment/config unless the
operator explicitly supplies --home.

SQLite transactions own immutable documents, logical-run deduplication, transitions,
approvals, deployment pointers, audit and an at-least-once outbox. Each run has a
renewed lease plus an OS file lock, preventing a paused live worker from writing
after another worker takes its expired lease. OS locks release after process death;
the persisted lease expires before restart. Training is serialized within a process
because PyTorch and NumPy RNG state is global. This is deliberately a single-host
worker design, not a distributed scheduler. Failed stages resume explicitly. The
resume command retries transient OS/time-out errors at most three times by
default (configurable up to five); policy/data errors never retry automatically.

Freeze whole-scene/region/grid groups before 256×256 tiling. Reproject footprints
to an equal-area CRS, group overlapping/buffered footprints, and group shared grid
blocks/regions. Independent random/scene/spatial experiments have separate MLflow
runs and checkpoints. Missing spatial metadata is REVIEW_REQUIRED and blocks
promotion under the default policy. This is stricter than a scene-only demo but
preserves the locked promotion invariant. Data with fewer than three independent
groups fails preparation rather than inventing validation/test evidence.

Use a small independently implemented shared-encoder Siamese U-Net trained from
scratch. The selected reference has an MIT badge but no verified root LICENSE;
do not copy its ResNet implementation or weights. Reject misaligned image pairs
instead of silently warping them; TIFF CRS/transforms and manifest bounds must
agree. PNG alignment/georeferencing remains a dataset producer responsibility.

Policy thresholds are configurable only by trusted host configuration and their
content digest is bound into lineage. Missing evidence and human approval cannot
be disabled. The report's claimed decision is never authoritative. Core immutable
model records remain pending; separate approval and active-pointer records derive
public approved/deployed states. A checksum/report/policy mismatch blocks activation.

Local canary means synchronous replay/health preflight, with compare-and-swap
activation and history; it is not a live-traffic experiment. AWS endpoint updates
use the native 10% blue/green capacity canary plus rollback alarms. First endpoint
creation has no previous fleet to canary against. Cloud adapter methods return
submission, not successful deployment; callers must check endpoint health.

The optional SageMaker Pipeline uses one bounded CPU Processing job to run the
same persisted lifecycle. It does not duplicate domain logic in an independent
SDK DAG. Its output retains reports, checkpoints, registry and tracking state;
approval/package registration/deployment remain explicit subsequent operations.
This trades independent SageMaker Training-step scaling for a small, testable v1
adapter. No SageMaker SDK dependency is needed beyond boto3's typed service model.

MCP uses the official stdio SDK with exactly six tools. Raw dispatch validates and
audits malformed arguments before SDK coercion. Anonymous local MCP is a reader;
configured credentials are resolved by the backend, and MCP cannot assert human
presence. A promotion request requires an existing human approval and creates only
a proposal. Deterministic incident investigation works without a paid LLM key.

Monitoring uses bounded scalar input features and absolute Wasserstein distance
plus Evidently reports. Regional prediction shift uses change fractions; labeled
performance is calculated only with sufficient feedback. These are practical
signals, not calibrated guarantees about model reliability. No drift handler
launches training automatically.

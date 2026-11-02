# AeroIntel — locked technical blueprint v1.0

Source: user-approved architecture supplied on 2026-11-02. This document records
the implementation baseline. Milestones are sequential, and architecture changes
require a superseding ADR.

## Product and scope

Build one integrated geospatial ML trust and operations platform. Its one v1 ML
task is building change detection from aligned before/after aerial image pairs.
Use a small Siamese U-Net. Inputs include image metadata and source-scene IDs.
Outputs will be probability masks, binary masks, change percentage, uncertainty,
review indicators and GeoJSON polygons only when valid geospatial metadata exists.
Start with a small LEVIR-CD+ subset after verifying dataset/model/source terms.

PropertyPulse owns imagery preparation, 256×256 tiling, alignment, normalization,
training and inference. GeoLeak owns duplicates, scene grouping, footprint overlap,
spatial block holdouts, independent split comparisons, region metrics and gates.
AWS MLOps owns the lifecycle through adapters. GeoShift owns input shift, regional
prediction shift and performance degradation when labels arrive. ModelOps owns a
restricted MCP control plane.

## Architecture and stack

Modular monolith, shared versioned contracts, dependency boundaries and optional
adapters. Python 3.12, PyTorch, Rasterio, GeoPandas, TorchGeo, FastAPI/Pydantic,
MLflow, Evidently, official MCP Python SDK, Boto3, Terraform, Pytest/Hypothesis,
OpenTelemetry, SQLite and Docker Compose. Evalora is an optional tracing integration.
TerraTorch and a second foundation model are future comparison options.

Local filesystem ↔ S3; local event dispatch ↔ EventBridge; SQLite jobs/events ↔ SQS;
local training ↔ SageMaker Pipelines; local approval ↔ SageMaker Model Registry;
FastAPI inference ↔ SageMaker endpoint; monitoring reports ↔ CloudWatch.
Both paths use identical business contracts and policy decisions.

Excluded: Kubernetes, Kafka, Redis, Celery, PostgreSQL, OpenSearch, Airflow, Bedrock,
a separate frontend, multiple ML tasks and unrestricted autonomous deployment.

## Contracts and lifecycle

Canonical contracts live in `specs/`: OpenAPI; dataset/model manifests;
evaluation/drift reports; six events; quality and operations policies; four ADRs.
Ownership: ingestion/dataset manifest, GeoLeak/evaluation report, registry/model
manifest, GeoShift/drift report, orchestrator/events, MCP server/operations policy.

Register dataset → validate/prepare → train → evaluate → gate → register passing
model pending approval → authorize approval → deploy → record predictions and
feedback → investigate drift. Successful training alone never activates a model.

Scene holdouts are required when no coordinates exist. Actual spatial block claims
require validated geographic metadata. Null CRS is legitimate. Compare independent
random, scene and spatial split experiments; never relabel one model's scores as
three independently trained experiments. Report IoU, Dice/F1, precision and recall.
Published examples and target thresholds are not measured results.

The MCP surface is exactly: get_pipeline_status, list_model_versions,
get_geoleak_report, get_drift_report, create_retraining_proposal, and
request_model_promotion. The first four read, the fifth proposes, the sixth requests
approval-gated promotion. Backend identity, policy, idempotency and audit enforce
the boundary. Drift creates an investigation, never an automatic retraining loop.

## Delivery order and acceptance

0. Architecture/contracts/config/Makefile/CI: reproducible foundation.
1. PropertyPulse baseline training, artifacts and working inference API.
2. GeoLeak automated leakage detection, splits, benchmarking and quality reports.
3. Local lifecycle with MLflow, registry, approval and promotion.
4. GeoShift reference/production monitoring, label feedback and proposals.
5. Six-tool MCP server, authorization, audit and incident investigation.
6. Minimal optional Terraform, S3, EventBridge/Lambda and SageMaker pipeline.
7. Failure injection, integration coverage, diagrams, demo and cost cleanup audit.

Required coverage includes invalid/corrupted images, missing metadata, duplicates,
overlapping tiles, scene leakage, invalid CRS, reproducibility, checkpoint recovery,
malformed masks, metric edge cases, spatial enforcement, duplicate model versions,
invalid artifacts, denied promotions, health checks, canary failure, shift signals,
missing labels, false alarms, invalid MCP arguments, authorization, idempotency,
audit history and the entire ingest-to-monitor path.

Critical invariants: a failed spatial gate blocks promotion despite excellent random
scores; an unapproved agent deployment request is rejected and audited.

## Cost and licensing

Local development and tests must work without AWS or paid services. Use moto for
supported AWS operations and botocore stubs/custom adapter mocks for SageMaker.
Terraform fmt/validate and static checks must not create resources. Cloud demo is
optional, short-lived, cost-reviewed and explicitly authorized; no persistent endpoints.
Do not rely on credit eligibility or Free Tier promises without rechecking them.

Study five references (AWS sample, Geo-Eval Harness, EarthShift, Urban Change
Detection, SageMaker MCP Server) outside the final repository. Verify actual
licenses, attribution obligations, model and data terms before reuse; do not merge
whole repositories. TorchGeo, optional TerraTorch, Evidently, MCP SDK and
OpenTelemetry are dependencies rather than forks. The referenced JD PDF was not
provided in this workspace and has not been reviewed.

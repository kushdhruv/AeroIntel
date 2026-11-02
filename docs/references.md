# Reference review and license ledger — 2026-11-02

Reviewed through read-only GitHub file APIs; no entire repositories were merged,
and no third-party source, checkpoints, imagery or notebooks were copied. If
cloning later, keep reference checkouts outside this repository as required by
the blueprint. This review uses architecture ideas with independent implementations.

| Source | Inspected material | Pattern adopted | License finding |
| --- | --- | --- | --- |
| [AWS sample](https://github.com/aws-samples/sample-sagemaker-image-classification-mlops) | README, LICENSE, scripts/mlops_common/gates.py and directory layout | Gate on untouched test data, pending manual approval, immutable execution paths, native canary/alarms | Root MIT-0 verified |
| [Geo-Eval Harness](https://github.com/torchgeo/geo-eval-harness) | README, LICENSE, repository/src layout | Independent random/spatial experiments, explicit reproducible configuration and resumable records | Root MIT verified |
| [EarthShift](https://github.com/kerner-lab/earthshift) | README, run_pipeline.py | Distinguish in/out-of-distribution evaluation and persist checkpoint/configuration | Root LICENSE path returned 404; dataset-specific terms in README; no reuse authorized |
| [Urban Change Detection](https://github.com/JeremyMaille/urban-change-detection) | README, src/models/siamese_unet.py | Shared encoder and absolute-difference skip concept; whole-scene splitting before patching | MIT badge in README, root LICENSE path returned 404; no source/weights copied |
| [SageMaker MCP Server](https://github.com/jam-jee/sagemaker-mcp-server) | README, src/sagemaker_mcp/server.py | Official FastMCP transport, backend validation, workflow history, lazy cloud access | Root LICENSE path returned 404; no source copied |

Source blob identifiers observed: AWS gate
`13616fae58e2601295b8dd5f8e802e739f1f0eb8`; Urban model
`7d76b394e73b4c05a809cff13906096baad8aec1`; Geo-Eval README
`78cbe5bf83225a6d155e8f4b2a8b5bd1091cd147` and LICENSE
`88e8af547844a0abf0c884d59f4e2629cb8000d7`. These are file blob IDs, not
claims that every reference was pinned to a verified commit SHA.

We deliberately do not adopt the reference MCP server's generic 200+ operation
executor, replay arbitrary cloud writes, the AWS sample's multiple models, or
EarthShift's external experiment account dependency. AeroIntel owns six domain
tools and a local MLflow backend. Those choices keep v1 narrow and credential-free.

LEVIR-CD+ has not been downloaded; its actual usage terms and location metadata
remain unverified. No claims about its geographic split quality or trained results
are made. The local synthetic fixture is generated from scratch. Before importing
real data or copying any reference source, record exact commit, source license,
required notices, model terms and dataset permissions. The project's own public
license remains an owner decision; upstream licenses do not automatically license
all new AeroIntel code.

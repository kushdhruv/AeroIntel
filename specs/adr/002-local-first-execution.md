# ADR 002: Local-first execution

Status: Accepted (blueprint v1.0).

Filesystem, SQLite, local training, MLflow, FastAPI and Evidently provide the
complete local path. No AWS credentials or credits are prerequisites. Optional
AWS adapters must satisfy identical contracts. Cloud provisioning requires a
cost review and explicit authorization; no always-running endpoints.

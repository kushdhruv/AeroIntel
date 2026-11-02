# ADR 003: Immutable versions and approval binding

Status: Accepted (blueprint v1.0).

Dataset/model IDs plus immutable versions identify artifacts; SHA-256 checks
bind content. An evaluation records dataset/model versions and split assignment
digest. Passing candidates enter pending_approval. Human approval binds exact
artifact digest, evaluation and policy. Deployment rechecks all bindings and
current authorization. Duplicate versions fail; retraining creates a new version.
Training success and gate PASS each remain insufficient for activation.

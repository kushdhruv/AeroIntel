# ADR 004: Backend-enforced agent boundaries

Status: Accepted (blueprint v1.0).

Expose four read tools and two request/proposal tools. Never expose unrestricted
SageMaker execution. Backend-authenticated identity, deny-by-default policy,
idempotency and an audit record govern writes. Agent output cannot grant approval.
Drift opens investigation; retraining and promotion require separate decisions.
Audit denied operations as well as successful ones. M0 contains policy contracts;
enforcement is delivered with the registry and MCP milestones.

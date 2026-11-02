# Security boundaries and limitations

External images/metadata, API bodies, artifact files, tool arguments and reports are
untrusted. Embedded report text is data and cannot alter operations policy. There
is no arbitrary shell/AWS operation tool and no LLM-supplied role or approval flag.

The HTTP backend resolves bearer tokens against a server-owned SHA-256 identity
map. Reader, operator and human-approver capabilities are distinct. The local CLI
uses the same credential map for approval/deployment; the synthetic demo has a
separate explicit approval flag. This bootstrap authentication is intended for
localhost development. Before exposure, replace it with expiring OIDC identities,
MFA/step-up approval, TLS, rate limits and an approved identity lifecycle.

MCP defaults to reader. Stdio operator credentials can propose, never self-approve.
Raw malformed arguments, unknown tools and denied writes are audited. Promotion
requests require existing human approval and create proposals without execution.
Audit records contain operation identity/outcome, not image bodies or credentials.
OpenTelemetry spans have no input/token attributes; exporters are host-configured.

Dataset paths are confined to storage/staging; traversal and symlink escapes are
rejected. Hash checks, image dimension/file/body limits, binary-mask validation and
safe `torch.load(weights_only=True)` reduce file/artifact risks. Model artifacts
are generated locally, immutable after training and checked again at activation.
Compromised local administrators or malicious concurrent filesystem writers remain
outside this threat boundary; path checks are not a sandbox against the host owner.

SQLite write transactions, idempotency fingerprints, file locks, leases, atomic
object writes and active-pointer CAS prevent ordinary duplicate/racing operations.
Audit triggers block application-level mutation; they do not create a tamper-proof
external audit archive. Back up state and configure immutable remote retention for
production. Inference/model loading and full dataset tiles remain CPU/memory bound;
this implementation has not been load-tested for untrusted multi-tenant traffic.

Terraform scopes S3 prefixes and IAM roles, blocks public bucket access, enforces
TLS/encryption, retains failures in an encrypted DLQ and disables job triggers by
default. Static scan exceptions are explicit demo decisions: service-managed
encryption, seven-day logs, no VPC/NAT, no cross-region replication/access-log bucket
or AWS Signer. They are tracked production hardening gaps, not hidden passes.

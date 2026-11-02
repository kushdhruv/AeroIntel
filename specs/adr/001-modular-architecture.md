# ADR 001: Modular monolith and contracts

Status: Accepted (blueprint v1.0).

One Python application with domain modules and storage/events/training adapters.
Avoid independently deployed services and duplicated local/cloud workflows.
`specs/` owns wire contracts; `contracts/` supplies validation. Modules do not own
alternative wire formats. Package boundaries can become services only if a real
operational requirement justifies that cost.

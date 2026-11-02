# Milestone 0 verification — 2026-11-02

Historical foundation record. Current implementation and final verification are
documented in [engineering-audit.md](engineering-audit.md) and
[results/verification.json](results/verification.json).

Runtime: Windows, CPython 3.12.11, workspace uv environment with committed lockfile.

| Check | Result |
| --- | --- |
| `python -m pytest -q` | 38 passed |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `mypy` | Passed, 34 source files |
| CLI synthetic dataset-manifest validation | Passed |
| Source distribution and wheel build | Passed; all 10 schemas and policies packaged |
| `docker compose --profile tracking config --quiet` | Passed |
| Docker build/startup | Unverified; Docker engine unavailable |
| GitHub Actions | Configured; not executed remotely |
| Training / benchmark / end-to-end lifecycle | Future milestones; no measured results |

Tests cover all ten schema definitions, contract versions, date formats, unknown
fields, metric ranges, null CRS, footprint/CRS pairing, event discriminators,
model approval presence, conservative gates and API/OpenAPI consistency.
The test of a forged PASS with excellent random IoU and failed spatial IoU returns
FAIL. This checks gate logic; registry promotion and MCP denial/audit acceptance
tests remain future milestones because those backends do not exist yet.

FastAPI TestClient hung under the local sandbox's event-loop restrictions, so the
successful suite ran with approved execution outside that sandbox. Dependency
download likewise required approved network access. Test output included a
Starlette httpx deprecation and an environment-specific pytest cache permission
warning; neither caused a failure. No cloud services were contacted or created.

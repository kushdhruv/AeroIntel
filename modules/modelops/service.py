"""Six-tool control plane. All writes validate policy before idempotent persistence."""

import json
from importlib.resources import files
from typing import Any, Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field

from adapters.storage.local import digest
from app.core.database import encode
from app.core.errors import Conflict, Forbidden, Invalid
from app.core.platform import Platform
from app.core.security import Principal, require
from app.core.telemetry import traced


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str = Field(min_length=1, max_length=200)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
    drift_report_id: str | None = Field(default=None, min_length=1, max_length=200)


ToolName = Literal[
    "get_pipeline_status",
    "list_model_versions",
    "get_geoleak_report",
    "get_drift_report",
    "create_retraining_proposal",
    "request_model_promotion",
]


class ModelOps:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.policy = yaml.safe_load(
            files("specs")
            .joinpath("policies", "operations-policy.yaml")
            .read_text(encoding="utf-8")
        )

    @traced("modelops.call")
    def call(self, tool: str, arguments: dict[str, Any], actor: Principal) -> Any:
        db = self.platform.db
        request_id = str(arguments.get("request_id", "invalid"))[:200]
        try:
            rule = self.policy.get("tools", {}).get(tool)
            if self.policy.get("default") != "deny" or not rule:
                raise Forbidden("Tool is not allowlisted by operations policy")
            require(actor, rule["role"])
            # Validate here, before Pydantic/FastMCP can drop extra fields unnoticed.
            if tool == "list_model_versions":
                if arguments:
                    raise Invalid("list_model_versions takes no arguments")
                require(actor, "reader")
                return self.platform.registry.versions()
            args = ToolArguments.model_validate(arguments)
            reads = {
                "get_pipeline_status": lambda: self.platform.pipeline.status(args.target),
                "get_geoleak_report": lambda: db.get("evaluation", args.target),
                "get_drift_report": lambda: db.get("drift", args.target),
            }
            if tool in reads:
                require(actor, "reader")
                if args.request_id or args.drift_report_id:
                    raise Invalid("Unexpected arguments for read tool")
                return reads[tool]()
            if tool not in {"create_retraining_proposal", "request_model_promotion"}:
                raise Forbidden("Tool is not allowlisted")
            require(actor, "operator")
            if not args.request_id:
                raise Invalid("Writes require an idempotency request_id")
            fingerprint = digest(encode([tool, arguments, actor.id]).encode())
            scope = f"mcp:{actor.id}:{tool}"
            with db.transaction() as connection:
                previous = connection.execute(
                    "SELECT fingerprint,result FROM dedup WHERE scope=? AND key=?",
                    (scope, args.request_id),
                ).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise Conflict("Idempotency key reused with different arguments")
                    return json.loads(previous[1])
            model = db.get("model", args.target)
            reason = "Approved model promotion requested; operator execution required"
            if tool == "request_model_promotion":
                if args.drift_report_id:
                    raise Invalid("Promotion does not accept drift evidence")
                self.platform.registry.approved(args.target)
            else:
                if not args.drift_report_id:
                    raise Invalid("Retraining requires a drift report")
                drift = db.get("drift", args.drift_report_id)
                if (
                    not drift["investigation_required"]
                    or drift["model_id"] != model["model_id"]
                    or drift["model_version"] != model["model_version"]
                ):
                    raise Forbidden("No matching actionable drift evidence")
                reason = f"Investigation required by drift report {args.drift_report_id}"
            result = {
                "proposal_id": str(uuid4()),
                "model_key": args.target,
                "type": tool,
                "state": "proposed",
                "reason": reason,
            }
            with db.transaction() as connection:
                # Serialize duplicate calls and retain the first committed response.
                prior = connection.execute(
                    "SELECT fingerprint,result FROM dedup WHERE scope=? AND key=?",
                    (scope, args.request_id),
                ).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise Conflict("Idempotency collision")
                    return json.loads(prior[1])
                db.put(connection, "proposal", result["proposal_id"], result)
                connection.execute(
                    "INSERT INTO dedup VALUES(?,?,?,?)",
                    (scope, args.request_id, fingerprint, encode(result)),
                )
                db.audit(
                    actor.id, tool, args.target, "allowed", reason, args.request_id, connection
                )
                if tool == "create_retraining_proposal":
                    db.event(
                        connection,
                        "retraining-proposed",
                        {
                            "proposal_id": result["proposal_id"],
                            "model_id": model["model_id"],
                            "model_version": model["model_version"],
                            "reason": reason,
                            "idempotency_key": args.request_id,
                        },
                        args.request_id,
                        actor.id,
                    )
            return result
        except Exception as exc:
            db.audit(
                actor.id,
                tool[:100],
                str(arguments.get("target", "unknown"))[:200],
                "denied",
                type(exc).__name__,
                request_id,
            )
            raise

"""Immutable registry content plus separately persisted approval decisions."""

from typing import Any
from uuid import uuid4

from adapters.storage.local import LocalStorage, digest
from app.core.database import Database, encode, now
from app.core.errors import Forbidden, Invalid, NotFound
from app.core.security import Principal, require
from contracts.validation import validate_contract
from modules.geoleak.gates import evaluate_gates, validate_policy


class Registry:
    def __init__(self, db: Database, storage: LocalStorage, policy: dict[str, Any]) -> None:
        import copy

        self.db, self.storage, self.policy = db, storage, copy.deepcopy(validate_policy(policy))

    def eligible(self, model: dict[str, Any]) -> dict[str, Any]:
        report = self.db.get("evaluation", model["evaluation_id"])
        for field in ("model_id", "model_version", "dataset_id", "dataset_version"):
            if report[field] != model[field]:
                raise Invalid(f"Evaluation lineage mismatch: {field}")
        if evaluate_gates(report, self.policy).decision != "PASS":
            raise Forbidden("Quality gate must PASS; review and failures block promotion")
        lineage = self.db.get("lineage", model["evaluation_id"])
        if digest(encode(lineage["splits"]).encode()) != report["split_assignment_sha256"]:
            raise Invalid("Evaluation split provenance mismatch")
        final = lineage["experiments"].get("spatial")
        if not final or final["metrics"] != report["splits"]["spatial"]:
            raise Invalid("Spatial evaluation does not match registered experiment")
        dataset = self.db.get("dataset", f"{model['dataset_id']}:{model['dataset_version']}")
        if lineage["dataset_sha256"] != digest(encode(dataset).encode()):
            raise Invalid("Dataset manifest lineage mismatch")
        if lineage["artifact_sha256"] != model["artifact_sha256"]:
            raise Invalid("Artifact does not match evaluated model")
        if lineage["policy_sha256"] != digest(encode(self.policy).encode()):
            raise Forbidden("Policy changed since evaluation; reevaluation required")
        self.storage.read(model["artifact_uri"], model["artifact_sha256"])
        return report

    def register(self, model: dict[str, Any]) -> None:
        validate_contract("model-manifest", model)
        if model["state"] != "pending_approval" or model["approval"] is not None:
            raise Invalid("New models must enter pending approval")
        self.eligible(model)
        with self.db.transaction() as db:
            self.db.put(db, "model", f"{model['model_id']}:{model['model_version']}", model)

    def approve(self, key: str, principal: Principal, request_id: str) -> dict[str, Any]:
        try:
            require(principal, "approver", human=True)
            model = self.db.get("model", key)
            report = self.eligible(model)
            from modules.ingestion.service import Ingestion

            Ingestion(self.db, self.storage).verify(
                self.db.get("dataset", f"{model['dataset_id']}:{model['dataset_version']}")
            )
            approval = {
                "approval_id": str(uuid4()),
                "actor_id": principal.id,
                "approved_at": now(),
                "artifact_sha256": model["artifact_sha256"],
                "evaluation_id": report["evaluation_id"],
                "policy_id": report["policy_id"],
            }
            with self.db.transaction() as db:
                try:
                    existing = self.db.get("approval", key, db)
                    return existing
                except NotFound:
                    pass
                self.db.put(db, "approval", key, approval)
                self.db.event(
                    db,
                    "model-approved",
                    {
                        "model_id": model["model_id"],
                        "model_version": model["model_version"],
                        "approval_id": approval["approval_id"],
                        "evaluation_id": report["evaluation_id"],
                        "artifact_sha256": model["artifact_sha256"],
                        "policy_id": report["policy_id"],
                    },
                    request_id,
                    principal.id,
                )
                self.db.audit(
                    principal.id, "approve", key, "allowed", "Human approval", request_id, db
                )
            return approval
        except Exception as exc:
            self.db.audit(principal.id, "approve", key, "denied", type(exc).__name__, request_id)
            raise

    def approved(self, key: str) -> dict[str, Any]:
        model = self.db.get("model", key)
        self.eligible(model)
        try:
            approval = self.db.get("approval", key)
        except NotFound as exc:
            raise Forbidden("Human approval is required before deployment") from exc
        for field in ("artifact_sha256", "evaluation_id"):
            if approval[field] != model[field]:
                raise Forbidden("Approval binding mismatch")
        if approval["policy_id"] != self.policy["policy_id"]:
            raise Forbidden("Approval policy mismatch")
        return model

    def versions(self) -> list[dict[str, Any]]:
        result = []
        for model in self.db.list("model"):
            key = f"{model['model_id']}:{model['model_version']}"
            try:
                model["approval"] = self.db.get("approval", key)
                model["state"] = "approved"
            except NotFound:
                pass
            with self.db.transaction() as db:
                active = db.execute("SELECT model_key FROM active WHERE slot='local'").fetchone()
            if active and active[0] == key:
                model["state"] = "deployed"
            validate_contract("model-manifest", model)
            result.append(model)
        return result

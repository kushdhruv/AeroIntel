"""Local replay canary, atomic activation, explicit rollback and deployment history."""

import json
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import numpy as np

from app.core.database import encode, now
from app.core.errors import Conflict, Invalid, NotFound
from app.core.security import Principal, require
from modules.ingestion.service import decode_image
from modules.propertypulse.inference.predict import predict
from modules.propertypulse.training.engine import confusion_metrics, load_model
from modules.registry.service import Registry


class LocalDeployment:
    def __init__(self, registry: Registry) -> None:
        self.registry, self.db, self.storage = registry, registry.db, registry.storage

    def active(self) -> str:
        with self.db.transaction() as db:
            row = db.execute("SELECT model_key FROM active WHERE slot='local'").fetchone()
        if not row:
            raise NotFound("No active local model")
        return str(row[0])

    def health(self, key: str) -> bool:
        model = self.registry.approved(key)
        network, config = load_model(self.storage.path(model["artifact_uri"]))
        manifest = self.db.get("dataset", f"{model['dataset_id']}:{model['dataset_version']}")
        lineage = self.db.get("lineage", model["evaluation_id"])
        test_ids = lineage["splits"]["spatial"]["test"]
        samples = [s for s in manifest["samples"] if s["sample_id"] in test_ids][:3]
        start = time.monotonic()
        tp = fp = fn = 0
        for sample in samples:
            images = [
                decode_image(
                    self.storage.read(sample["image_pair"][name], sample["checksum"][name])
                )
                for name in ("before", "after")
            ]
            output = predict(network, images[0], images[1], config.threshold)
            if not np.isfinite(output["probability_mask"]).all():
                return False
            predicted = output["mask"].astype(bool)
            truth: Any = decode_image(
                self.storage.read(sample["mask"], sample["checksum"]["mask"]), True
            ).astype(bool)
            tp += int((predicted & truth).sum())
            fp += int((predicted & ~truth).sum())
            fn += int((~predicted & truth).sum())
        required = self.registry.policy["minimum_metrics"]["spatial"]["iou"]
        return bool(
            samples
            and confusion_metrics(tp, fp, fn)["iou"] >= required
            and time.monotonic() - start < 10
        )

    def deploy(
        self,
        key: str,
        actor: Principal,
        request_id: str,
        canary: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        try:
            require(actor, "operator")
            self.registry.approved(key)
            fingerprint = encode({"key": key, "actor": actor.id})
            with self.db.transaction() as db:
                prior = db.execute(
                    "SELECT fingerprint,result FROM dedup WHERE scope='deploy' AND key=?",
                    (request_id,),
                ).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise Conflict("Idempotency key reused with different deployment")
                    return dict(json.loads(prior[1]))
                active = db.execute("SELECT * FROM active WHERE slot='local'").fetchone()
                previous = active["model_key"] if active else None
                previous_id = active["deployment_id"] if active else None
            deployment_id = str(uuid4())
            result = {
                "deployment_id": deployment_id,
                "model_key": key,
                "previous_key": previous,
                "started_at": now(),
                "canary_type": "local_replay",
                "status": "failed",
            }
            try:
                healthy = (canary or self.health)(key)
            except Exception:
                healthy = False
            if healthy:
                self.registry.approved(key)
            with self.db.transaction() as db:
                duplicate = db.execute(
                    "SELECT fingerprint,result FROM dedup WHERE scope='deploy' AND key=?",
                    (request_id,),
                ).fetchone()
                if duplicate:
                    if duplicate[0] != fingerprint:
                        raise Conflict("Idempotency collision")
                    return dict(json.loads(duplicate[1]))
                current = db.execute(
                    "SELECT deployment_id FROM active WHERE slot='local'"
                ).fetchone()
                if (current[0] if current else None) != previous_id:
                    raise Conflict(
                        "Active deployment changed during canary; retry with a new request"
                    )
                if healthy:
                    result["status"] = "active"
                    db.execute(
                        "INSERT INTO active VALUES('local',?,?) ON CONFLICT(slot) DO UPDATE "
                        "SET model_key=excluded.model_key,deployment_id=excluded.deployment_id",
                        (key, deployment_id),
                    )
                db.execute(
                    "INSERT INTO deployments VALUES(?,?,?,?,?)",
                    (
                        deployment_id,
                        key,
                        previous,
                        result["status"],
                        encode(result),
                    ),
                )
                db.execute(
                    "INSERT INTO dedup VALUES('deploy',?,?,?)",
                    (request_id, fingerprint, encode(result)),
                )
                self.db.audit(
                    actor.id,
                    "deploy",
                    key,
                    "allowed" if healthy else "denied",
                    "Replay canary passed" if healthy else "Replay canary failed",
                    request_id,
                    db,
                )
            return result
        except Exception as exc:
            self.db.audit(actor.id, "deploy", key, "denied", type(exc).__name__, request_id)
            raise

    def rollback(self, actor: Principal, request_id: str) -> dict[str, Any]:
        require(actor, "operator")
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT d.previous_key FROM active a JOIN deployments d "
                "ON a.deployment_id=d.id WHERE a.slot='local'"
            ).fetchone()
        if not row or not row[0]:
            raise Invalid("No previous deployment to roll back to")
        return self.deploy(str(row[0]), actor, request_id)

    def history(self) -> list[dict[str, Any]]:
        with self.db.transaction() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM deployments")]

"""Prediction provenance, immutable feedback, conservative monitoring and evidence."""

import io
import os
from datetime import datetime
from typing import Any
from uuid import uuid4

import numpy as np
from scipy.stats import wasserstein_distance

from adapters.storage.local import digest
from app.core.database import encode, now
from app.core.errors import Conflict, Invalid, NotFound
from app.core.security import Principal, require
from app.core.telemetry import traced
from contracts.validation import validate_contract
from modules.ingestion.service import (
    decode_image,
    validate_inference_georeferencing,
    validate_metadata,
)
from modules.propertypulse.inference.predict import predict
from modules.propertypulse.training.engine import confusion_metrics, load_model
from modules.registry.deployment import LocalDeployment


def features(before: np.ndarray, after: np.ndarray) -> list[float]:
    return [float(value) for value in after.mean(axis=(0, 1))] + [
        float(np.abs(after - before).mean())
    ]


class Production:
    def __init__(self, deployment: LocalDeployment) -> None:
        self.deployment, self.db = deployment, deployment.db
        self.storage = deployment.storage

    @traced("inference.predict")
    def infer(
        self,
        before_raw: bytes,
        after_raw: bytes,
        region: str,
        request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = self.deployment.active()
        fingerprint = digest(before_raw + after_raw + encode([key, region, metadata]).encode())
        try:
            previous = self.db.get("prediction", request_id)
            if previous["fingerprint"] != fingerprint:
                raise Conflict("Prediction idempotency key reused with different inputs/model")
            return self._output(previous)
        except NotFound:
            pass
        if metadata:
            validate_metadata(metadata)
        before, after = decode_image(before_raw), decode_image(after_raw)
        validate_inference_georeferencing(before_raw, after_raw, metadata)
        model = self.deployment.registry.approved(key)
        network, config = load_model(self.storage.path(model["artifact_uri"]))
        output = predict(network, before, after, config.threshold, metadata)
        stream = io.BytesIO()
        np.savez_compressed(
            stream, probability_mask=output.pop("probability_mask"), mask=output.pop("mask")
        )
        object_key = f"predictions/{fingerprint[7:]}.npz"
        self.storage.write(object_key, stream.getvalue())
        record = {
            "prediction_id": request_id,
            "model_key": key,
            "created_at": now(),
            "region_id": region,
            "features": features(before, after),
            "fingerprint": fingerprint,
            "array_uri": object_key,
            "array_sha256": digest(stream.getvalue()),
            "shape": list(before.shape[:2]),
            **output,
        }
        with self.db.transaction() as db:
            self.db.put(db, "prediction", request_id, record)
        return self._output(record)

    def _output(self, record: dict[str, Any]) -> dict[str, Any]:
        arrays = np.load(
            io.BytesIO(self.storage.read(record["array_uri"], record["array_sha256"])),
            allow_pickle=False,
        )
        return {**record, "mask": arrays["mask"], "probability_mask": arrays["probability_mask"]}

    def feedback(self, prediction_id: str, mask: bytes, actor: Principal) -> None:
        try:
            require(actor, "operator")
            record = self.db.get("prediction", prediction_id)
            target = decode_image(mask, True)
            if list(target.shape) != record["shape"]:
                raise Invalid("Ground-truth mask shape differs from prediction")
            checksum = digest(mask)
            key = f"feedback/{checksum[7:]}.png"
            self.storage.write(key, mask)
            with self.db.transaction() as db:
                self.db.put(
                    db,
                    "feedback",
                    prediction_id,
                    {
                        "prediction_id": prediction_id,
                        "mask_uri": key,
                        "sha256": checksum,
                    },
                )
                self.db.audit(actor.id, "feedback", prediction_id, "allowed", checksum, db=db)
        except Exception as exc:
            self.db.audit(actor.id, "feedback", prediction_id, "denied", type(exc).__name__)
            raise


class Monitoring:
    def __init__(self, production: Production) -> None:
        self.production, self.db, self.storage = production, production.db, production.storage

    @traced("monitoring.run")
    def run(
        self,
        model_key: str,
        start: str,
        end: str,
        minimum_samples: int = 5,
        drift_threshold: float = 0.15,
        minimum_labels: int = 3,
    ) -> dict[str, Any]:
        start_at, end_at = (
            datetime.fromisoformat(start.replace("Z", "+00:00")),
            datetime.fromisoformat(end.replace("Z", "+00:00")),
        )
        if (
            start_at.utcoffset() is None
            or end_at.utcoffset() is None
            or start_at >= end_at
            or minimum_samples < 3
            or minimum_labels < 1
            or not 0 < drift_threshold < 1
        ):
            raise Invalid("Invalid monitoring window or thresholds")
        model = self.db.get("model", model_key)
        manifest = self.db.get("dataset", f"{model['dataset_id']}:{model['dataset_version']}")
        report_id = str(uuid4())
        current = [
            p
            for p in self.db.list("prediction")
            if p["model_key"] == model_key
            and start_at <= datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")) <= end_at
        ]
        reference = self._reference(model_key, model, manifest)
        sufficient = len(current) >= minimum_samples and len(reference) >= minimum_samples
        columns = ["red_mean", "green_mean", "blue_mean", "absolute_change"]
        scores = self._scores(reference, current) if sufficient else None
        input_shift = "detected" if max(scores or [0]) > drift_threshold else "not_detected"
        if not sufficient:
            input_shift = "insufficient_data"
        regions = []
        for region in sorted({p["region_id"] for p in current}):
            before = [r["fraction"] for r in reference if r["region_id"] == region]
            after = [p["change_percentage"] / 100 for p in current if p["region_id"] == region]
            status = "insufficient_data"
            if len(before) >= minimum_samples and len(after) >= minimum_samples:
                status = (
                    "detected"
                    if wasserstein_distance(before, after) > drift_threshold
                    else "not_detected"
                )
            regions.append({"region_id": region, "status": status})
        performance = self._performance(current, model, minimum_labels)
        report_uri = None
        if sufficient:
            os.environ.setdefault("DO_NOT_TRACK", "1")
            import pandas as pd
            from evidently import Report
            from evidently.presets import DataDriftPreset

            snapshot = Report(
                [DataDriftPreset(columns=columns, method="wasserstein", threshold=drift_threshold)]
            ).run(
                current_data=pd.DataFrame([p["features"] for p in current], columns=columns),
                reference_data=pd.DataFrame([r["features"] for r in reference], columns=columns),
            )
            report_uri = f"monitoring/{report_id}/evidently.json"
            self.storage.write(report_uri, snapshot.json().encode())
            snapshot.save_html(str(self.storage.path(f"monitoring/{report_id}/evidently.html")))
        report = {
            "schema_version": "1.0.0",
            "report_id": report_id,
            "model_id": model["model_id"],
            "model_version": model["model_version"],
            "created_at": now(),
            "reference_dataset_version": manifest["dataset_version"],
            "window_start": start,
            "window_end": end,
            "input_shift": input_shift,
            "regional_prediction_shift": regions,
            "performance": performance,
            "evidently_report_uri": report_uri,
            "investigation_required": (
                input_shift == "detected"
                or any(r["status"] == "detected" for r in regions)
                or performance["status"] == "degraded"
            ),
        }
        validate_contract("drift-report", report)
        with self.db.transaction() as db:
            self.db.put(db, "drift", report_id, report)
            self.db.put(
                db,
                "drift-evidence",
                report_id,
                {
                    "reference_count": len(reference),
                    "production_count": len(current),
                    "absolute_wasserstein": dict(zip(columns, scores or [], strict=False)),
                    "threshold": drift_threshold,
                    "minimum_samples": minimum_samples,
                    "minimum_labels": minimum_labels,
                },
            )
            if report["investigation_required"]:
                self.db.event(
                    db,
                    "drift-detected",
                    {
                        "report_id": report_id,
                        "model_id": model["model_id"],
                        "model_version": model["model_version"],
                    },
                    report_id,
                    "monitoring",
                )
        return report

    def _reference(
        self, key: str, model: dict[str, Any], manifest: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            return list(self.db.get("reference", key)["samples"])
        except NotFound:
            pass
        self.production.deployment.registry.approved(key)
        network, config = load_model(self.storage.path(model["artifact_uri"]))
        result = []
        for sample in manifest["samples"]:
            before, after = [
                decode_image(
                    self.storage.read(sample["image_pair"][name], sample["checksum"][name])
                )
                for name in ("before", "after")
            ]
            prediction = predict(network, before, after, config.threshold)
            result.append(
                {
                    "region_id": sample["metadata"]["region_id"] or "unknown",
                    "features": features(before, after),
                    "fraction": prediction["change_percentage"] / 100,
                }
            )
        with self.db.transaction() as db:
            self.db.put(db, "reference", key, {"samples": result})
        return result

    @staticmethod
    def _scores(reference: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[float]:
        return [
            float(
                wasserstein_distance(
                    [r["features"][i] for r in reference], [p["features"][i] for p in current]
                )
            )
            for i in range(4)
        ]

    def _performance(
        self, current: list[dict[str, Any]], model: dict[str, Any], minimum_labels: int
    ) -> dict[str, Any]:
        count = tp = fp = fn = 0
        for record in current:
            try:
                feedback = self.db.get("feedback", record["prediction_id"])
            except NotFound:
                continue
            truth: Any = decode_image(
                self.storage.read(feedback["mask_uri"], feedback["sha256"]), True
            ).astype(bool)
            prediction = self.production._output(record)["mask"].astype(bool)
            count += 1
            tp += int((prediction & truth).sum())
            fp += int((prediction & ~truth).sum())
            fn += int((~prediction & truth).sum())
        if count < minimum_labels:
            return {
                "status": "missing_labels" if count == 0 else "insufficient_data",
                "label_count": count,
                "iou": None,
                "f1": None,
            }
        metrics = confusion_metrics(tp, fp, fn)
        baseline = self.db.get("evaluation", model["evaluation_id"])["splits"]["spatial"]["iou"]
        return {
            "status": "degraded" if metrics["iou"] < baseline - 0.1 else "stable",
            "label_count": count,
            "iou": metrics["iou"],
            "f1": metrics["f1"],
        }

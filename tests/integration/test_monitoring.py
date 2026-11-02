import io

import numpy as np
import pytest
from PIL import Image

from app.core.database import now
from app.core.errors import Forbidden
from app.core.platform import Platform
from app.core.security import Principal
from modules.modelops.service import ModelOps


def activate(platform: Platform) -> str:
    model = platform.db.list("model")[0]
    key = f"{model['model_id']}:{model['model_version']}"
    platform.registry.approve(key, Principal("human", "approver", True), "approve")
    assert (
        platform.deployment.deploy(key, Principal("operator", "operator"), "deploy")["status"]
        == "active"
    )
    return key


def test_reference_replay_no_false_drift_and_shift_without_labels(trained: Platform) -> None:
    key = activate(trained)
    samples = trained.db.list("dataset")[0]["samples"]
    for index, sample in enumerate(samples):
        trained.production.infer(
            trained.storage.read(sample["image_pair"]["before"]),
            trained.storage.read(sample["image_pair"]["after"]),
            sample["metadata"]["region_id"],
            f"reference-{index}",
        )
    baseline = trained.monitoring.run(key, "2020-01-01T00:00:00Z", now())
    assert baseline["input_shift"] == "not_detected"
    assert baseline["investigation_required"] is False
    assert baseline["performance"] == {
        "status": "missing_labels",
        "label_count": 0,
        "iou": None,
        "f1": None,
    }
    start = now()
    raw = io.BytesIO()
    Image.new("RGB", (64, 64), (255, 255, 255)).save(raw, format="PNG")
    for index in range(6):
        trained.production.infer(raw.getvalue(), raw.getvalue(), "shifted", f"shift-{index}")
    shifted = trained.monitoring.run(key, start, now())
    assert shifted["input_shift"] == "detected" and shifted["investigation_required"]
    assert shifted["performance"]["status"] == "missing_labels"
    assert shifted["performance"]["iou"] is None
    assert trained.storage.path(shifted["evidently_report_uri"]).exists()
    ops = ModelOps(trained)
    arguments = {"target": key, "request_id": "retrain", "drift_report_id": shifted["report_id"]}
    proposal = ops.call("create_retraining_proposal", arguments, Principal("operator", "operator"))
    assert proposal == ops.call(
        "create_retraining_proposal", arguments, Principal("operator", "operator")
    )
    assert proposal["state"] == "proposed"
    with pytest.raises(Forbidden):
        ops.call(
            "create_retraining_proposal",
            {**arguments, "request_id": "unnecessary", "drift_report_id": baseline["report_id"]},
            Principal("operator", "operator"),
        )


def test_feedback_computes_real_performance_and_checks_shape(trained: Platform) -> None:
    key = activate(trained)
    samples = trained.db.list("dataset")[0]["samples"][:3]
    operator = Principal("operator", "operator")
    for index, sample in enumerate(samples):
        trained.production.infer(
            trained.storage.read(sample["image_pair"]["before"]),
            trained.storage.read(sample["image_pair"]["after"]),
            sample["metadata"]["region_id"],
            str(index),
        )
        # Incorrect human labels intentionally establish real degradation evidence.
        stream = io.BytesIO()
        Image.fromarray(np.zeros((64, 64), dtype="uint8")).save(stream, format="PNG")
        trained.production.feedback(str(index), stream.getvalue(), operator)
    result = trained.monitoring.run(key, "2020-01-01T00:00:00Z", now())
    assert result["performance"]["status"] == "degraded"
    assert result["performance"]["label_count"] == 3
    assert result["performance"]["iou"] == 0

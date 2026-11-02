import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.core.database import now
from app.core.errors import Forbidden
from app.core.platform import Platform
from app.core.security import Principal
from modules.ingestion.synthetic import generate
from modules.modelops.service import ModelOps
from modules.propertypulse.training.engine import TrainingConfig


def run_demo(root: Path, approve: bool = False, epochs: int = 12) -> dict[str, Any]:
    platform = Platform(root)
    manifest = generate(root / "incoming")
    platform.ingestion.register(manifest, root / "incoming")
    key = f"{manifest['dataset_id']}:{manifest['dataset_version']}"
    run_id = platform.pipeline.submit(key, TrainingConfig(epochs=epochs))
    status = platform.pipeline.execute(run_id)
    report = platform.db.get("evaluation", run_id)
    result = {
        "run_id": run_id,
        "pipeline_state": status["state"],
        "evaluation": report,
        "synthetic_only": True,
    }
    if status["state"] != "COMPLETED":
        return result
    model_key = status["result"]["model_key"]
    if not approve:
        result["next_action"] = "Explicit approval needed; rerun demo with --approve-demo"
        return result
    operator = Principal("demo-operator", "operator")
    platform.registry.approve(model_key, Principal("demo-human", "approver", True), "demo-approval")
    deployment = platform.deployment.deploy(model_key, operator, f"demo-deploy-{run_id}")
    if deployment["status"] != "active":
        raise Forbidden("Demo canary failed")
    stored_manifest = platform.db.get("dataset", key)
    start = "2020-01-01T00:00:00Z"
    for sample in stored_manifest["samples"]:
        before = platform.storage.read(sample["image_pair"]["before"])
        after = platform.storage.read(sample["image_pair"]["after"])
        prediction_id = f"demo-{run_id}-{sample['sample_id']}"
        platform.production.infer(before, after, sample["metadata"]["region_id"], prediction_id)
        platform.production.feedback(prediction_id, platform.storage.read(sample["mask"]), operator)
    baseline = platform.monitoring.run(model_key, start, now())
    shift_start = now()
    for index, sample in enumerate(stored_manifest["samples"][:6]):
        raws = []
        for field in ("before", "after"):
            raw = platform.storage.read(sample["image_pair"][field])
            image = np.array(Image.open(io.BytesIO(raw))).astype(np.int16)
            stream = io.BytesIO()
            Image.fromarray(np.clip(image + 160, 0, 255).astype(np.uint8)).save(
                stream, format="PNG"
            )
            raws.append(stream.getvalue())
        platform.production.infer(
            raws[0], raws[1], sample["metadata"]["region_id"], f"shift-{run_id}-{index}"
        )
    drift = platform.monitoring.run(model_key, shift_start, now())
    proposal = (
        ModelOps(platform).call(
            "create_retraining_proposal",
            {
                "target": model_key,
                "request_id": f"demo-retrain-{run_id}",
                "drift_report_id": drift["report_id"],
            },
            operator,
        )
        if drift["investigation_required"]
        else None
    )
    result.update(
        {
            "model_key": model_key,
            "deployment": deployment,
            "reference_replay_monitoring": baseline,
            "shifted_monitoring": drift,
            "retraining_proposal": proposal,
            "audit_events": len(platform.db.history()),
        }
    )
    return result

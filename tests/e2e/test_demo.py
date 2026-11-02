from pathlib import Path

from app.cli.demo import run_demo
from app.core.platform import Platform


def test_complete_synthetic_lifecycle(tmp_path: Path) -> None:
    result = run_demo(tmp_path / "demo", approve=True)
    assert result["pipeline_state"] == "COMPLETED"
    assert result["evaluation"]["decision"] == "PASS"
    assert result["deployment"]["status"] == "active"
    assert result["reference_replay_monitoring"]["input_shift"] == "not_detected"
    assert result["reference_replay_monitoring"]["performance"]["status"] == "stable"
    assert result["shifted_monitoring"]["input_shift"] == "detected"
    assert result["shifted_monitoring"]["performance"]["iou"] is None
    assert result["retraining_proposal"]["state"] == "proposed"
    # Reopen the composition root: state, model lineage and active deployment survive restart.
    reopened = Platform(tmp_path / "demo")
    assert reopened.deployment.active() == result["model_key"]
    assert reopened.pipeline.status(result["run_id"])["state"] == "COMPLETED"
    assert len(reopened.db.list("prediction")) == 30

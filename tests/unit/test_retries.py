import pytest

from app.core.errors import Invalid
from app.core.platform import Platform


def test_bounded_transient_retries_and_no_policy_retry(tmp_path, monkeypatch) -> None:
    platform = Platform(tmp_path)
    attempts = []

    def transient(run_id):
        attempts.append(run_id)
        if len(attempts) < 3:
            raise OSError("Injected storage interruption")
        return {"state": "COMPLETED"}

    monkeypatch.setattr(platform.pipeline, "execute", transient)
    assert platform.pipeline.execute_with_retries("test")["state"] == "COMPLETED"
    assert len(attempts) == 3

    def invalid(run_id):
        raise Invalid("Policy failure is not transient")

    monkeypatch.setattr(platform.pipeline, "execute", invalid)
    with pytest.raises(Invalid):
        platform.pipeline.execute_with_retries("test")


def test_lineage_and_mlflow_tracking_match_artifact(trained: Platform) -> None:
    model = trained.db.list("model")[0]
    lineage = trained.db.get("lineage", model["evaluation_id"])
    assert lineage["artifact_sha256"] == model["artifact_sha256"]
    for experiment in lineage["experiments"].values():
        run = trained.pipeline.tracking.client.get_run(experiment["mlflow_run_id"])
        assert run.info.status == "FINISHED"
        assert run.data.metrics["test_iou"] == experiment["metrics"]["iou"]
        assert run.data.params["seed"] == "42"

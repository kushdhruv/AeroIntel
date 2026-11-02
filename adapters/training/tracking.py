"""Explicit MLflow client, without global active runs or external credentials."""

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


class Tracking:
    def __init__(self, root: Path) -> None:
        from mlflow import MlflowClient

        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.uri = f"sqlite:///{(root / 'mlflow.db').as_posix()}"
        self.client = MlflowClient(tracking_uri=self.uri)
        experiment = self.client.get_experiment_by_name("AeroIntel")
        self.experiment_id = (
            experiment.experiment_id
            if experiment
            else self.client.create_experiment(
                "AeroIntel",
                artifact_location=(root / "artifacts").as_uri(),
            )
        )

    def start(self, parameters: dict[str, Any], tags: dict[str, str]) -> str:
        run = self.client.create_run(self.experiment_id, tags=tags)
        for key, value in parameters.items():
            self.client.log_param(run.info.run_id, key, value)
        return str(run.info.run_id)

    def log(self, run_id: str, step: int, loss: float, metrics: dict[str, float]) -> None:
        self.client.log_metric(run_id, "loss", loss, step=step)
        for key, value in metrics.items():
            self.client.log_metric(run_id, f"validation_{key}", value, step=step)

    def finish(self, run_id: str, artifact: Path, metrics: dict[str, Any]) -> None:
        self.client.log_artifact(run_id, str(artifact))
        for key, value in metrics.items():
            self.client.log_metric(run_id, f"test_{key}", float(value))
        self.client.set_terminated(run_id, "FINISHED")

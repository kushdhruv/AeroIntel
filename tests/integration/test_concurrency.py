import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import Conflict
from app.core.platform import Platform
from app.core.security import Principal
from modules.propertypulse.training.engine import TrainingConfig


def test_concurrent_duplicate_deployments_commit_once(trained: Platform) -> None:
    model = trained.db.list("model")[0]
    key = f"{model['model_id']}:{model['model_version']}"
    trained.registry.approve(key, Principal("reviewer", "approver", True), "approve")
    barrier = threading.Barrier(2)

    def canary(_: str) -> bool:
        barrier.wait(timeout=10)
        return True

    def deploy(_: int) -> dict:
        return trained.deployment.deploy(key, Principal("operator", "operator"), "same", canary)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(deploy, range(2)))
    assert results[0] == results[1]
    assert len(trained.deployment.history()) == 1


def test_live_worker_cannot_be_stolen_even_after_lease_expiry(trained: Platform) -> None:
    dataset = trained.db.list("dataset")[0]
    run_id = trained.pipeline.submit(
        f"{dataset['dataset_id']}:{dataset['dataset_version']}", TrainingConfig(epochs=2)
    )
    entered, release = threading.Event(), threading.Event()

    def pause(stage: str) -> None:
        if stage == "validate":
            entered.set()
            assert release.wait(15)
            raise RuntimeError("Injected stopped worker")

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(trained.pipeline.execute, run_id, pause)
        try:
            assert entered.wait(10)
            with trained.db.transaction() as db:
                db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, run_id))
            with pytest.raises(Conflict):
                trained.pipeline.execute(run_id)
        finally:
            release.set()
        with pytest.raises(RuntimeError):
            worker.result()
    assert trained.pipeline.status(run_id)["state"] == "FAILED"


def test_dead_worker_lease_can_be_recovered(trained: Platform) -> None:
    dataset = trained.db.list("dataset")[0]
    run_id = trained.pipeline.submit(
        f"{dataset['dataset_id']}:{dataset['dataset_version']}", TrainingConfig(epochs=1)
    )
    with trained.db.transaction() as db:
        db.execute(
            "UPDATE runs SET state='RUNNING',owner='dead-worker',lease_until=0 WHERE id=?",
            (run_id,),
        )
    assert trained.pipeline.execute(run_id)["state"] in {"COMPLETED", "REJECTED"}

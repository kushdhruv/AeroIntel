"""Durable, leased state machine for one local workflow and independent benchmarks."""

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from filelock import FileLock, Timeout

from adapters.storage.local import LocalStorage, digest, file_digest
from adapters.training.tracking import Tracking
from app.core.database import Database, encode, now
from app.core.errors import Conflict, Invalid, NotFound
from app.core.telemetry import traced
from contracts.validation import validate_contract
from modules.geoleak.gates import evaluate_gates
from modules.geoleak.splits import leakage, make_split
from modules.ingestion.service import Ingestion
from modules.propertypulse.datasets.paired import PairedTiles
from modules.propertypulse.training.engine import TrainingConfig, evaluate, load_model, train
from modules.registry.service import Registry

STAGES = ("validate", "prepare", "train", "evaluate", "register", "done")
TERMINAL = {"COMPLETED", "REJECTED"}


class Pipeline:
    def __init__(self, db: Database, storage: LocalStorage, registry: Registry) -> None:
        self.db, self.storage, self.registry = db, storage, registry
        self._tracking: Tracking | None = None

    @property
    def tracking(self) -> Tracking:
        if self._tracking is None:
            self._tracking = Tracking(self.storage.root / "experiments")
        return self._tracking

    def submit(self, dataset_key: str, config: TrainingConfig) -> str:
        dataset = self.db.get("dataset", dataset_key)
        parameters = {
            "dataset_key": dataset_key,
            "training": config.model_dump(),
            "dataset_sha256": digest(encode(dataset).encode()),
            "policy_sha256": digest(encode(self.registry.policy).encode()),
        }
        logical_key = digest(encode(parameters).encode())
        with self.db.transaction() as db:
            row = db.execute("SELECT id FROM runs WHERE logical_key=?", (logical_key,)).fetchone()
            if row:
                return str(row[0])
            run_id = str(uuid4())
            db.execute(
                "INSERT INTO runs(id,logical_key,state,stage,config) VALUES(?,?,?,?,?)",
                (run_id, logical_key, "QUEUED", "validate", encode(parameters)),
            )
            self.db.audit("pipeline", "submit", run_id, "allowed", logical_key, db=db)
            return run_id

    def consume_dataset_event(self, event: dict[str, Any], config: TrainingConfig) -> str:
        validate_contract("dataset-batch-completed", event)
        payload = event["payload"]
        key = f"{payload['dataset_id']}:{payload['dataset_version']}"
        manifest = self.db.get("dataset", key)
        raw = self.storage.read(payload["manifest_uri"], payload["manifest_sha256"])
        if json.loads(raw) != manifest:
            raise Invalid("Event manifest differs from registered dataset")
        return self.submit(key, config)

    def status(self, run_id: str) -> dict[str, Any]:
        with self.db.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise NotFound("Pipeline run not found")
        result = dict(row)
        result["config"], result["result"] = json.loads(row["config"]), json.loads(row["result"])
        result.pop("owner")
        return result

    def _save_result(self, run_id: str, owner: str, result: dict[str, Any]) -> None:
        with self.db.transaction() as db:
            changed = db.execute(
                "UPDATE runs SET result=? WHERE id=? AND owner=? AND state='RUNNING'",
                (encode(result), run_id, owner),
            ).rowcount
            if changed != 1:
                raise Conflict("Pipeline lease lost")

    def _claim(self, run_id: str, owner: str, lease_seconds: float) -> bool:
        with self.db.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise NotFound("Pipeline run not found")
            if row["state"] in TERMINAL:
                return False
            if (
                row["state"] not in {"QUEUED", "FAILED", "RUNNING"}
                or row["stage"] not in STAGES[:-1]
            ):
                raise Invalid("Invalid persisted pipeline state")
            if row["owner"] and row["lease_until"] > time.time():
                raise Conflict("Pipeline is already owned by a live worker")
            db.execute(
                "UPDATE runs SET state='RUNNING',owner=?,lease_until=?,error=NULL,"
                "attempts=attempts+1 WHERE id=?",
                (owner, time.time() + lease_seconds, run_id),
            )
            self.db.audit(
                "pipeline", "claim", run_id, "allowed", f"resume at {row['stage']}", db=db
            )
        return True

    def execute_with_retries(self, run_id: str, max_attempts: int = 3) -> dict[str, Any]:
        if not 1 <= max_attempts <= 5:
            raise Invalid("Retry attempts must be between one and five")
        for attempt in range(max_attempts):
            try:
                return self.execute(run_id)
            except (OSError, TimeoutError):
                if attempt + 1 == max_attempts:
                    raise
                time.sleep(0.05 * 2**attempt)
        raise AssertionError("unreachable")

    @traced("pipeline.execute")
    def execute(
        self, run_id: str, failpoint: Callable[[str], None] | None = None, lease_seconds: float = 30
    ) -> dict[str, Any]:
        lock_path = self.storage.path(f"locks/{digest(run_id.encode())[7:]}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(lock_path)
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise Conflict("A worker is already executing this run") from exc
        try:
            return self._execute(run_id, failpoint, lease_seconds)
        finally:
            lock.release()

    def _execute(
        self, run_id: str, failpoint: Callable[[str], None] | None, lease_seconds: float
    ) -> dict[str, Any]:
        if lease_seconds < 1:
            raise Invalid("Lease must be at least one second")
        owner, stopped = str(uuid4()), threading.Event()
        if not self._claim(run_id, owner, lease_seconds):
            return self.status(run_id)

        def heartbeat() -> None:
            while not stopped.wait(lease_seconds / 3):
                try:
                    with self.db.transaction() as db:
                        db.execute(
                            "UPDATE runs SET lease_until=? WHERE id=? AND owner=?",
                            (time.time() + lease_seconds, run_id, owner),
                        )
                except sqlite3.Error:
                    # The next fenced write prevents stale workers from committing.
                    continue

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            status = self.status(run_id)
            parameters, result = status["config"], status["result"]
            manifest = self.db.get("dataset", parameters["dataset_key"])
            if digest(encode(manifest).encode()) != parameters["dataset_sha256"]:
                raise Invalid("Dataset lineage changed")
            if digest(encode(self.registry.policy).encode()) != parameters["policy_sha256"]:
                raise Invalid("Pipeline quality policy changed")
            config = TrainingConfig.model_validate(parameters["training"])
            for stage in STAGES[STAGES.index(status["stage"]) : -1]:
                if failpoint:
                    failpoint(stage)
                if stage == "validate":
                    Ingestion(self.db, self.storage).verify(manifest)
                elif stage == "prepare":
                    result["splits"] = {}
                    for method in ("random", "scene", "spatial"):
                        if method == "spatial" and not all(
                            sample["metadata"]["crs"] and sample["metadata"]["region_id"]
                            for sample in manifest["samples"]
                        ):
                            continue
                        result["splits"][method] = make_split(
                            manifest["samples"],
                            method,
                            config.seed,
                            config.block_meters,
                        )
                    result["split_sha256"] = digest(encode(result["splits"]).encode())
                    self.storage.write(
                        f"runs/{run_id}/splits.json", encode(result["splits"]).encode()
                    )
                elif stage == "train":
                    self._train(run_id, owner, result, manifest, config)
                elif stage == "evaluate":
                    self._evaluate(run_id, result, manifest)
                elif stage == "register":
                    report = self.db.get("evaluation", run_id)
                    if evaluate_gates(report, self.registry.policy).decision == "PASS":
                        final = result["experiments"]["spatial"]
                        model = {
                            "schema_version": "1.0.0",
                            "model_id": "propertypulse",
                            "model_version": run_id,
                            "architecture": "siamese_unet",
                            "dataset_id": manifest["dataset_id"],
                            "dataset_version": manifest["dataset_version"],
                            "artifact_uri": final["artifact"],
                            "artifact_sha256": final["sha256"],
                            "evaluation_id": run_id,
                            "created_at": report["created_at"],
                            "state": "pending_approval",
                            "approval": None,
                        }
                        self.registry.register(model)
                        result["model_key"] = f"propertypulse:{run_id}"
                next_stage = STAGES[STAGES.index(stage) + 1]
                state = "RUNNING"
                if next_stage == "done":
                    state = "COMPLETED" if result.get("model_key") else "REJECTED"
                with self.db.transaction() as db:
                    changed = db.execute(
                        "UPDATE runs SET stage=?,state=?,result=? "
                        "WHERE id=? AND owner=? AND stage=? AND state='RUNNING'",
                        (next_stage, state, encode(result), run_id, owner, stage),
                    )
                    if changed.rowcount != 1:
                        raise Conflict("Invalid transition or pipeline lease lost")
                    self.db.audit("pipeline", "stage_completed", run_id, "allowed", stage, db=db)
            return self.status(run_id)
        except BaseException as exc:
            with self.db.transaction() as db:
                db.execute(
                    "UPDATE runs SET state='FAILED',error=? WHERE id=? AND owner=? "
                    "AND state='RUNNING'",
                    (f"{type(exc).__name__}: {exc}", run_id, owner),
                )
                self.db.audit("pipeline", "failed", run_id, "denied", type(exc).__name__, db=db)
            raise
        finally:
            stopped.set()
            thread.join(timeout=lease_seconds)
            with self.db.transaction() as db:
                db.execute(
                    "UPDATE runs SET owner=NULL,lease_until=0 WHERE id=? AND owner=?",
                    (run_id, owner),
                )

    def _train(
        self,
        run_id: str,
        owner: str,
        result: dict[str, Any],
        manifest: dict[str, Any],
        config: TrainingConfig,
    ) -> None:
        experiments = result.setdefault("experiments", {})
        for method, splits in result["splits"].items():
            experiment = experiments.setdefault(method, {})
            if "artifact" in experiment:
                self.storage.read(experiment["artifact"], experiment["sha256"])
                continue
            if "mlflow_run_id" not in experiment:
                experiment["mlflow_run_id"] = self.tracking.start(
                    config.model_dump(),
                    {
                        "pipeline_id": run_id,
                        "split": method,
                        "dataset_id": manifest["dataset_id"],
                        "dataset_version": manifest["dataset_version"],
                        "split_assignment_sha256": result["split_sha256"],
                    },
                )
                self._save_result(run_id, owner, result)
            datasets = {
                fold: PairedTiles(
                    [sample for sample in manifest["samples"] if sample["sample_id"] in ids],
                    self.storage,
                )
                for fold, ids in splits.items()
            }

            def log_epoch(
                step: int,
                loss: float,
                values: dict[str, float],
                tracking_id: str = experiment["mlflow_run_id"],
            ) -> None:
                self.tracking.log(tracking_id, step, loss, values)

            artifact, history = train(
                datasets["train"],
                datasets["validation"],
                config,
                self.storage,
                f"runs/{run_id}/{method}",
                log_epoch,
            )
            network, _ = load_model(self.storage.path(artifact))
            metrics: dict[str, Any] = evaluate(network, datasets["test"], config.threshold)
            metrics["test_samples"] = len(splits["test"])
            self.tracking.finish(experiment["mlflow_run_id"], self.storage.path(artifact), metrics)
            regional = {}
            test = [s for s in manifest["samples"] if s["sample_id"] in splits["test"]]
            for region in sorted({s["metadata"]["region_id"] or "unknown" for s in test}):
                members = [s for s in test if (s["metadata"]["region_id"] or "unknown") == region]
                regional[region] = evaluate(
                    network, PairedTiles(members, self.storage), config.threshold
                )
            experiment.update(
                {
                    "artifact": artifact,
                    "sha256": file_digest(self.storage.path(artifact)),
                    "metrics": metrics,
                    "history": history,
                    "regional_metrics": regional,
                }
            )
            self._save_result(run_id, owner, result)

    def _evaluate(self, run_id: str, result: dict[str, Any], manifest: dict[str, Any]) -> None:
        checks = [
            leakage(manifest["samples"], split)
            for method, split in result["splits"].items()
            if method != "random"
        ]
        combined = {
            field: max(check[field] for check in checks)
            for field in ("duplicate_pairs", "scene_leaks", "overlapping_tiles")
        }
        combined["overlap_check_status"] = (
            "completed"
            if all(c["overlap_check_status"] == "completed" for c in checks)
            else "unavailable"
        )
        report = {
            "schema_version": "1.0.0",
            "evaluation_id": run_id,
            "dataset_id": manifest["dataset_id"],
            "dataset_version": manifest["dataset_version"],
            "model_id": "propertypulse",
            "model_version": run_id,
            "created_at": now(),
            "policy_id": self.registry.policy["policy_id"],
            "geographic_metadata_available": "spatial" in result["splits"],
            "split_assignment_sha256": result["split_sha256"],
            "splits": {
                method: result["experiments"].get(method, {}).get("metrics")
                for method in ("random", "scene", "spatial")
            },
            "leakage": combined,
            "decision": "REVIEW_REQUIRED",
            "reasons": [],
            "limitations": ["Synthetic fixtures establish software behavior only"]
            if manifest["source"] == "aerointel-synthetic"
            else [],
        }
        if "spatial" not in result["splits"]:
            report["limitations"].append("No reliable geographic evaluation is available")
        decision = evaluate_gates(report, self.registry.policy)
        report.update({"decision": decision.decision, "reasons": list(decision.reasons)})
        final = result["experiments"].get("spatial", result["experiments"]["scene"])
        with self.db.transaction() as db:
            # A crash between report persistence and stage completion must reuse the same timestamp.
            try:
                report = self.db.get("evaluation", run_id, db)
            except NotFound:
                self.db.put(db, "evaluation", run_id, report)
                self.db.put(
                    db,
                    "lineage",
                    run_id,
                    {
                        "artifact_sha256": final["sha256"],
                        "dataset_sha256": digest(encode(manifest).encode()),
                        "experiments": result["experiments"],
                        "splits": result["splits"],
                        "policy_sha256": digest(encode(self.registry.policy).encode()),
                    },
                )
                self.db.event(
                    db,
                    "training-completed",
                    {
                        "job_id": run_id,
                        "model_id": "propertypulse",
                        "model_version": run_id,
                        "artifact_uri": final["artifact"],
                        "artifact_sha256": final["sha256"],
                    },
                    run_id,
                )
                self.db.event(
                    db,
                    "evaluation-completed",
                    {
                        "evaluation_id": run_id,
                        "report_uri": f"runs/{run_id}/evaluation.json",
                        "decision": report["decision"],
                    },
                    run_id,
                )
        self.storage.write(f"runs/{run_id}/evaluation.json", encode(report).encode())

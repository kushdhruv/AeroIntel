"""Deterministic CPU training with safe checkpoints and independent experiments."""

import io
import random
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import torch
from pydantic import BaseModel, ConfigDict, Field
from torch.nn import functional as F
from torch.utils.data import DataLoader

from adapters.storage.local import LocalStorage
from app.core.errors import Invalid
from modules.propertypulse.datasets.paired import PairedTiles
from modules.propertypulse.models.siamese import SiameseUNet

TRAINING_LOCK = threading.Lock()


class TrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    epochs: int = Field(default=12, ge=1, le=1000)
    batch_size: int = Field(default=4, ge=1, le=32)
    learning_rate: float = Field(default=0.01, gt=0, le=0.1)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    width: int = Field(default=4, ge=2, le=32)
    threshold: float = Field(default=0.5, gt=0, lt=1)
    block_meters: float = Field(default=10_000, gt=0)


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def tensor_bytes(value: Any) -> bytes:
    stream = io.BytesIO()
    torch.save(value, stream)
    return stream.getvalue()


def confusion_metrics(tp: int, fp: int, fn: int) -> dict[str, float]:
    if min(tp, fp, fn) < 0:
        raise Invalid("Confusion counts cannot be negative")
    return {
        "iou": tp / (tp + fp + fn) if tp + fp + fn else 1.0,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0,
        "precision": tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0),
        "recall": tp / (tp + fn) if tp + fn else 1.0,
    }


@torch.no_grad()
def evaluate(model: SiameseUNet, dataset: PairedTiles, threshold: float) -> dict[str, float]:
    model.eval()
    tp = fp = fn = 0
    for before, after, target, valid in DataLoader(dataset, batch_size=4):
        prediction = model(before, after).sigmoid() >= threshold
        truth, keep = target.bool(), valid.bool()
        tp += int((prediction & truth & keep).sum())
        fp += int((prediction & ~truth & keep).sum())
        fn += int((~prediction & truth & keep).sum())
    return confusion_metrics(tp, fp, fn)


def train(
    dataset: PairedTiles,
    validation: PairedTiles,
    config: TrainingConfig,
    storage: LocalStorage,
    prefix: str,
    log: Callable[[int, float, dict[str, float]], None],
) -> tuple[str, list[dict[str, Any]]]:
    # RNG state is process-global; serialize local training to preserve reproducibility.
    with TRAINING_LOCK:
        return _train(dataset, validation, config, storage, prefix, log)


def _train(
    dataset: PairedTiles,
    validation: PairedTiles,
    config: TrainingConfig,
    storage: LocalStorage,
    prefix: str,
    log: Callable[[int, float, dict[str, float]], None],
) -> tuple[str, list[dict[str, Any]]]:
    seed_everything(config.seed)
    model = SiameseUNet(config.width)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    start, best, history = 0, -1.0, []
    checkpoint_key = f"{prefix}/checkpoint.pt"
    if storage.path(checkpoint_key).exists():
        saved = torch.load(storage.path(checkpoint_key), map_location="cpu", weights_only=True)
        if saved["config"] != config.model_dump():
            raise Invalid("Checkpoint configuration differs from this run")
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        start, best, history = saved["epoch"], saved["best"], saved["history"]
    for epoch in range(start, config.epochs):
        # Epoch-local ordering makes restart independent of global RNG consumption.
        generator = torch.Generator().manual_seed(config.seed + epoch)
        model.train()
        losses = []
        for before, after, target, valid in DataLoader(
            dataset,
            batch_size=config.batch_size,
            shuffle=True,
            generator=generator,
        ):
            optimizer.zero_grad()
            logits = model(before, after)
            bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none") * valid
            probabilities = logits.sigmoid() * valid
            dice = 1 - (2 * (probabilities * target).sum() + 1) / (
                probabilities.sum() + (target * valid).sum() + 1
            )
            loss = bce.sum() / valid.sum() + dice
            if not torch.isfinite(loss):
                raise Invalid("Non-finite training loss")
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach()))
        metrics = evaluate(model, validation, config.threshold)
        mean_loss = sum(losses) / len(losses)
        history.append({"epoch": epoch + 1, "loss": mean_loss, **metrics})
        if metrics["f1"] > best:
            best = metrics["f1"]
            storage.write(
                f"{prefix}/best.pt",
                tensor_bytes(
                    {
                        "model": model.state_dict(),
                        "config": config.model_dump(),
                    }
                ),
                immutable=False,
            )
        storage.write(
            checkpoint_key,
            tensor_bytes(
                {
                    "model": model.state_dict(),
                    "optimizer": optimizer.state_dict(),
                    "epoch": epoch + 1,
                    "best": best,
                    "history": history,
                    "config": config.model_dump(),
                }
            ),
            immutable=False,
        )
        log(epoch + 1, mean_loss, metrics)
    best_model = torch.load(
        storage.path(f"{prefix}/best.pt"), map_location="cpu", weights_only=True
    )
    artifact = f"{prefix}/model.pt"
    # Crash retries reuse the already-frozen artifact instead of serializing it again.
    if not storage.path(artifact).exists():
        storage.write(artifact, tensor_bytes(best_model))
    return artifact, history


def load_model(path: Path) -> tuple[SiameseUNet, TrainingConfig]:
    saved = torch.load(path, map_location="cpu", weights_only=True)
    config = TrainingConfig.model_validate(saved["config"])
    model = SiameseUNet(config.width)
    model.load_state_dict(saved["model"], strict=True)
    if any(not torch.isfinite(parameter).all() for parameter in model.parameters()):
        raise Invalid("Artifact contains non-finite parameters")
    return model.eval(), config

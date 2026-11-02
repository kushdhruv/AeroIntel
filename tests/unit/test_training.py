from pathlib import Path

import pytest
import torch

from app.core.platform import Platform
from modules.ingestion.synthetic import generate
from modules.propertypulse.datasets.paired import PairedTiles
from modules.propertypulse.inference.predict import predict
from modules.propertypulse.training.engine import TrainingConfig, load_model, train


def test_checkpoint_resume_matches_uninterrupted_training(tmp_path: Path) -> None:
    platform = Platform(tmp_path / "platform")
    manifest = generate(tmp_path / "incoming")
    data = platform.ingestion.register(manifest, tmp_path / "incoming")
    training = PairedTiles(data["samples"][:4], platform.storage)
    validation = PairedTiles(data["samples"][4:6], platform.storage)
    config = TrainingConfig(epochs=3)

    def interrupt(step: int, loss: float, metrics: dict) -> None:
        if step == 1:
            raise RuntimeError("Crash after durable epoch checkpoint")

    with pytest.raises(RuntimeError):
        train(training, validation, config, platform.storage, "interrupted", interrupt)
    resumed, history = train(
        training, validation, config, platform.storage, "interrupted", lambda *_: None
    )
    direct, direct_history = train(
        training, validation, config, platform.storage, "direct", lambda *_: None
    )
    first, _ = load_model(platform.storage.path(resumed))
    second, _ = load_model(platform.storage.path(direct))
    assert history == direct_history
    assert all(
        torch.equal(value, second.state_dict()[name]) for name, value in first.state_dict().items()
    )


def test_unreferenced_partial_upload_never_registers_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = Platform(tmp_path / "platform")
    manifest = generate(tmp_path / "source")
    original, count = platform.storage.write, 0

    def fail_upload(key: str, data: bytes, immutable: bool = True) -> str:
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("Injected full disk")
        return original(key, data, immutable)

    monkeypatch.setattr(platform.storage, "write", fail_upload)
    with pytest.raises(OSError):
        platform.ingestion.register(manifest, tmp_path / "source")
    assert platform.db.list("dataset") == []
    with platform.db.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


def test_inference_tiles_odd_dimensions_and_no_fake_geojson(trained: Platform) -> None:
    import numpy as np

    model = trained.db.list("model")[0]
    network, config = load_model(trained.storage.path(model["artifact_uri"]))
    before = np.zeros((259, 267, 3), dtype="float32")
    after = before.copy()
    after[10:30, 15:35] = 1
    result = predict(network, before, after, config.threshold)
    assert result["mask"].shape == (259, 267)
    assert result["probability_mask"].shape == (259, 267)
    assert result["geojson"] is None
    assert 0 <= result["uncertainty"] <= 1

import shutil
from pathlib import Path

import pytest

from app.core.platform import Platform
from modules.ingestion.synthetic import generate
from modules.propertypulse.training.engine import TrainingConfig


@pytest.fixture(scope="session")
def trained_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("trained")
    platform = Platform(root)
    manifest = generate(root / "incoming")
    platform.ingestion.register(manifest, root / "incoming")
    run_id = platform.pipeline.submit(
        f"{manifest['dataset_id']}:{manifest['dataset_version']}", TrainingConfig()
    )
    result = platform.pipeline.execute(run_id)
    assert result["state"] == "COMPLETED", platform.db.get("evaluation", run_id)
    return root


@pytest.fixture
def trained(trained_root: Path, tmp_path: Path) -> Platform:
    root = tmp_path / "platform"
    shutil.copytree(trained_root, root)
    return Platform(root)

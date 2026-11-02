import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.cli.main import main
from app.core.platform import Platform
from app.core.security import Principal


def test_cli_honors_configured_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "configured"
    monkeypatch.setenv("AEROINTEL_HOME", str(home))
    monkeypatch.setattr("sys.argv", ["aerointel", "status", "missing"])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1
    assert (home / "metadata.db").is_file()


def test_cli_predict_and_rollback(
    trained: Platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = trained.db.list("model")[0]
    key = f"{model['model_id']}:{model['model_version']}"
    token = "test-operator-cli"
    identities = {hashlib.sha256(token.encode()).hexdigest(): {"id": "op", "role": "operator"}}
    monkeypatch.setenv("AEROINTEL_IDENTITIES", json.dumps(identities))
    monkeypatch.setenv("AEROINTEL_TOKEN", token)
    monkeypatch.setenv("AEROINTEL_HOME", str(trained.root))

    # Approve and deploy model first
    trained.registry.approve(key, Principal("human", "approver", True), "cli-appr")
    trained.deployment.deploy(key, Principal("op", "operator"), "cli-dep")

    # Create dummy images
    b_path = tmp_path / "b.png"
    a_path = tmp_path / "a.png"
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(stream, format="PNG")
    b_path.write_bytes(stream.getvalue())
    a_path.write_bytes(stream.getvalue())

    # Test predict CLI
    monkeypatch.setattr("sys.argv", ["aerointel", "predict", str(b_path), str(a_path)])
    main()

    # Test monitor CLI
    monkeypatch.setattr("sys.argv", ["aerointel", "monitor", key])
    main()

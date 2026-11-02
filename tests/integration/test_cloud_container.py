import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapters.training.cloud_worker import run
from app.core.platform import Platform
from modules.propertypulse.inference.sagemaker import app, loaded


def test_inference_container_contract_and_artifact_digest(trained: Platform, monkeypatch) -> None:
    model = trained.db.list("model")[0]
    monkeypatch.setenv("SM_MODEL_DIR", str(trained.storage.path(model["artifact_uri"]).parent))
    monkeypatch.setenv("AEROINTEL_ARTIFACT_SHA256", model["artifact_sha256"])
    loaded.cache_clear()
    client = TestClient(app)
    assert client.get("/ping").status_code == 200
    sample = trained.db.list("dataset")[0]["samples"][0]
    response = client.post(
        "/invocations",
        json={
            name: base64.b64encode(trained.storage.read(sample["image_pair"][name])).decode()
            for name in ("before", "after")
        },
    )
    assert response.status_code == 200
    assert len(response.json()["mask"]) == 64
    assert (
        client.post("/invocations", json={"before": "invalid", "after": "invalid"}).status_code
        == 422
    )
    loaded.cache_clear()
    monkeypatch.setenv("AEROINTEL_ARTIFACT_SHA256", "sha256:" + "0" * 64)
    assert client.get("/ping").status_code != 200
    loaded.cache_clear()


@pytest.mark.parametrize(
    "uri",
    [
        "s3://other/aerointel/input/manifest.json",
        "https://example.com/data",
        "s3://approved/unconfined/manifest.json",
    ],
)
def test_cloud_worker_rejects_unconfined_inputs_before_sdk_call(tmp_path: Path, uri: str) -> None:
    with pytest.raises(ValueError, match="configured bucket"):
        run(uri, None, "approved", tmp_path)

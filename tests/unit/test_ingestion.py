import copy
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from adapters.storage.local import digest
from app.core.errors import Conflict, Invalid
from app.core.platform import Platform
from modules.ingestion.service import decode_image, validate_metadata
from modules.ingestion.synthetic import generate


def test_duplicate_ingest_has_one_manifest_and_event(tmp_path: Path) -> None:
    platform = Platform(tmp_path / "platform")
    manifest = generate(tmp_path / "source")
    first = platform.ingestion.register(manifest, tmp_path / "source")
    assert platform.ingestion.register(manifest, tmp_path / "source") == first
    assert len(platform.db.list("dataset")) == 1
    with platform.db.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    manifest["source"] = "changed"
    with pytest.raises(Conflict):
        platform.ingestion.register(manifest, tmp_path / "source")


@pytest.mark.parametrize("change", ["terms", "hash", "missing-mask", "duplicate-id", "date", "crs"])
def test_bad_manifest_never_emits_completed_event(tmp_path: Path, change: str) -> None:
    manifest = generate(tmp_path / "source")
    sample = manifest["samples"][0]
    if change == "terms":
        manifest["usage_terms"]["status"] = "unverified"
    elif change == "hash":
        sample["checksum"]["before"] = "sha256:" + "0" * 64
    elif change == "missing-mask":
        sample["mask"] = None
    elif change == "duplicate-id":
        manifest["samples"].append(copy.deepcopy(sample))
    elif change == "date":
        sample["metadata"]["acquisition_before"] = "2026-01-01"
    else:
        sample["metadata"]["crs"] = "EPSG:999999999"
    platform = Platform(tmp_path / "platform")
    with pytest.raises(Invalid):
        platform.ingestion.register(manifest, tmp_path / "source")
    assert platform.db.list("dataset") == []
    with platform.db.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


@pytest.mark.parametrize("raw", [b"broken image", b"II*\x00corrupt tiff", b""])
def test_corrupted_images_rejected(raw: bytes) -> None:
    with pytest.raises(Invalid):
        decode_image(raw)


def test_nonbinary_mask_and_misaligned_pair_rejected(tmp_path: Path) -> None:
    stream = io.BytesIO()
    Image.fromarray(np.full((16, 16), 42, dtype="uint8")).save(stream, format="PNG")
    with pytest.raises(Invalid):
        decode_image(stream.getvalue(), True)
    manifest = generate(tmp_path / "source")
    sample = manifest["samples"][0]
    stream = io.BytesIO()
    Image.new("RGB", (32, 64)).save(stream, format="PNG")
    path = tmp_path / "source" / sample["image_pair"]["after"]
    path.write_bytes(stream.getvalue())
    sample["checksum"]["after"] = digest(stream.getvalue())
    with pytest.raises(Invalid, match="matching dimensions"):
        Platform(tmp_path / "platform").ingestion.register(manifest, tmp_path / "source")


def test_input_snapshots_survive_source_mutation(tmp_path: Path) -> None:
    manifest = generate(tmp_path / "source")
    platform = Platform(tmp_path / "platform")
    snapshot = platform.ingestion.register(manifest, tmp_path / "source")
    (tmp_path / "source" / manifest["samples"][0]["image_pair"]["before"]).write_bytes(b"changed")
    platform.ingestion.verify(snapshot)
    assert json.dumps(snapshot) != json.dumps(manifest)


def test_invalid_geographic_bounds_rejected() -> None:
    with pytest.raises(Invalid):
        validate_metadata(
            {
                "crs": "EPSG:4326",
                "bounds": [190, 1, 200, 2],
                "acquisition_before": None,
                "acquisition_after": None,
            }
        )

from pathlib import Path

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from adapters.storage.local import digest
from app.core.errors import Invalid
from app.core.platform import Platform
from modules.ingestion.service import raster_metadata, validate_inference_georeferencing
from modules.ingestion.synthetic import generate


def tiff(x: float) -> bytes:
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=64,
            width=64,
            count=3,
            dtype="uint8",
            crs="EPSG:4326",
            transform=from_origin(x, 35, 0.001, 0.001),
        ) as dataset:
            dataset.write(np.ones((3, 64, 64), dtype="uint8"))
        return memory.read()


def test_tiff_alignment_checked_against_actual_headers(tmp_path: Path) -> None:
    manifest = generate(tmp_path / "incoming")
    sample = manifest["samples"][0]
    for field, x in (("before", -120.0), ("after", -119.0)):
        raw = tiff(x)
        (tmp_path / "incoming" / sample["image_pair"][field]).write_bytes(raw)
        sample["checksum"][field] = digest(raw)
    header = raster_metadata(tiff(-120))
    assert header is not None
    sample["metadata"]["bounds"] = list(header[2])
    with pytest.raises(Invalid, match="CRS/transforms"):
        Platform(tmp_path / "platform").ingestion.register(manifest, tmp_path / "incoming")


def test_inference_rejects_misaligned_tiffs() -> None:
    with pytest.raises(Invalid, match="align before inference"):
        validate_inference_georeferencing(tiff(-120), tiff(-119), None)


def test_rotated_grid_is_rejected_instead_of_emitting_wrong_geojson() -> None:
    from rasterio.transform import Affine

    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=16,
            width=16,
            count=3,
            dtype="uint8",
            crs="EPSG:4326",
            transform=Affine(0.001, 0.0001, -120, 0, -0.001, 35),
        ) as dataset:
            dataset.write(np.ones((3, 16, 16), dtype="uint8"))
        with pytest.raises(Invalid, match="north-up"):
            raster_metadata(memory.read())

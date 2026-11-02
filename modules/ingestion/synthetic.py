"""Generated fixtures only: no licensed imagery and no real geographic evidence."""

import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from adapters.storage.local import LocalStorage, digest
from app.core.database import encode


def png(array: np.ndarray) -> bytes:
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return stream.getvalue()


def generate(root: Path, seed: int = 17) -> dict[str, Any]:
    storage, rng = LocalStorage(root), np.random.default_rng(seed)
    samples = []
    for region in range(6):
        for scene in range(2):
            for tile in range(2):
                name = f"r{region}-s{scene}-t{tile}"
                before = rng.integers(15, 45, (64, 64, 3), dtype=np.uint8)
                after = before.copy()
                mask: Any = np.zeros((64, 64), dtype=np.uint8)
                y, x = rng.integers(5, 25, size=2)
                h, w = rng.integers(18, 32, size=2)
                after[y : y + h, x : x + w] = rng.integers(215, 255, (h, w, 3), dtype=np.uint8)
                mask[y : y + h, x : x + w] = 255
                paths, checksums = {}, {}
                for field, array in (("before", before), ("after", after), ("mask", mask)):
                    raw = png(array)
                    paths[field] = storage.write(f"{name}/{field}.png", raw)
                    checksums[field] = digest(raw)
                lon, lat = -120 + region * 2 + tile * 0.002, 35 + scene * 0.002
                samples.append(
                    {
                        "sample_id": name,
                        "source_scene_id": f"r{region}-s{scene}",
                        "image_pair": {k: paths[k] for k in ("before", "after")},
                        "mask": paths["mask"],
                        "checksum": checksums,
                        "metadata": {
                            "region_id": f"synthetic-region-{region}",
                            "crs": "EPSG:4326",
                            "bounds": [lon, lat, lon + 0.001, lat + 0.001],
                            "acquisition_before": "2024-01-01",
                            "acquisition_after": "2025-01-01",
                        },
                    }
                )
    manifest = {
        "schema_version": "1.0.0",
        "dataset_id": "synthetic-buildings",
        "dataset_version": f"seed-{seed}",
        "source": "aerointel-synthetic",
        "usage_terms": {"status": "verified", "reference": "Generated locally by AeroIntel"},
        "split_policy": "spatial_block_holdout",
        "samples": samples,
    }
    storage.write("manifest.json", encode(manifest).encode())
    return manifest

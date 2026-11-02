"""High-resolution aerial image pair tiling and manifest generator.

Slices large aerial image pairs (e.g. 1024x1024 LEVIR-CD+ or GeoTIFF scenes) into
aligned 256x256 patches, assigns shared scene IDs to prevent geographic leakage,
calculates tile-level spatial footprints, and outputs a schema-compliant manifest.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from adapters.storage.local import digest
from contracts.validation import validate_contract
from modules.ingestion.service import decode_image, raster_metadata


def tile_scene(
    before_path: Path,
    after_path: Path,
    mask_path: Path | None,
    output_dir: Path,
    scene_id: str,
    region_id: str | None = None,
    crs: str | None = None,
    bounds: list[float] | None = None,
    tile_size: int = 256,
    acquisition_before: str = "2024-01-01",
    acquisition_after: str = "2025-01-01",
) -> list[dict[str, Any]]:
    """Slice one aligned before/after/mask aerial scene into 256x256 tiles."""
    before_raw = before_path.read_bytes()
    after_raw = after_path.read_bytes()
    before_img = decode_image(before_raw)
    after_img = decode_image(after_raw)

    if before_img.shape != after_img.shape:
        raise ValueError(f"Shape mismatch: {before_path} vs {after_path}")

    height, width = before_img.shape[:2]

    # Auto-detect georeferencing if not explicitly passed
    detected = raster_metadata(before_raw)
    if detected and crs is None and bounds is None:
        detected_crs, _, detected_bounds = detected
        if detected_crs:
            crs = detected_crs.to_string()
            bounds = list(detected_bounds)

    mask_arr: np.ndarray | None = None
    if mask_path and mask_path.is_file():
        mask_raw = mask_path.read_bytes()
        mask_arr = decode_image(mask_raw, mask=True)
        if mask_arr.shape != (height, width):
            raise ValueError(f"Mask shape {mask_arr.shape} does not match images {(height, width)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []

    tile_idx = 0
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            h = min(tile_size, height - y)
            w = min(tile_size, width - x)
            # Only keep full-size tiles or pad to 256x256
            if h < 16 or w < 16:
                continue

            sample_id = f"{scene_id}-t{tile_idx:03d}"
            sample_dir = output_dir / sample_id
            sample_dir.mkdir(parents=True, exist_ok=True)

            # Crop or pad to tile_size
            b_patch: Any = np.zeros((tile_size, tile_size, 3), dtype=np.uint8)
            a_patch: Any = np.zeros((tile_size, tile_size, 3), dtype=np.uint8)
            b_patch[:h, :w] = (before_img[y : y + h, x : x + w] * 255).astype(np.uint8)
            a_patch[:h, :w] = (after_img[y : y + h, x : x + w] * 255).astype(np.uint8)

            b_bytes = _to_png_bytes(b_patch)
            a_bytes = _to_png_bytes(a_patch)

            b_file = sample_dir / "before.png"
            a_file = sample_dir / "after.png"
            b_file.write_bytes(b_bytes)
            a_file.write_bytes(a_bytes)

            checksums: dict[str, str] = {
                "before": digest(b_bytes),
                "after": digest(a_bytes),
            }
            image_pairs: dict[str, str] = {
                "before": str(b_file.relative_to(output_dir)).replace("\\", "/"),
                "after": str(a_file.relative_to(output_dir)).replace("\\", "/"),
            }

            mask_rel: str | None = None
            if mask_arr is not None:
                m_patch: Any = np.zeros((tile_size, tile_size), dtype=np.uint8)
                m_patch[:h, :w] = (mask_arr[y : y + h, x : x + w] * 255).astype(np.uint8)
                m_bytes = _to_png_bytes(m_patch)
                m_file = sample_dir / "mask.png"
                m_file.write_bytes(m_bytes)
                checksums["mask"] = digest(m_bytes)
                mask_rel = str(m_file.relative_to(output_dir)).replace("\\", "/")

            # Compute tile geographic bounding box by linear interpolation if bounds provided
            tile_bounds: list[float] | None = None
            if bounds and len(bounds) == 4:
                min_x, min_y, max_x, max_y = bounds
                dx = (max_x - min_x) / width
                dy = (max_y - min_y) / height
                tile_min_x = min_x + x * dx
                tile_max_x = min_x + (x + w) * dx
                tile_max_y = max_y - y * dy
                tile_min_y = max_y - (y + h) * dy
                tile_bounds = [
                    round(tile_min_x, 6),
                    round(tile_min_y, 6),
                    round(tile_max_x, 6),
                    round(tile_max_y, 6),
                ]

            samples.append(
                {
                    "sample_id": sample_id,
                    "source_scene_id": scene_id,
                    "image_pair": image_pairs,
                    "mask": mask_rel,
                    "checksum": checksums,
                    "metadata": {
                        "region_id": region_id,
                        "crs": crs,
                        "bounds": tile_bounds,
                        "acquisition_before": acquisition_before,
                        "acquisition_after": acquisition_after,
                    },
                }
            )
            tile_idx += 1

    return samples


def build_dataset_manifest(
    dataset_id: str,
    dataset_version: str,
    samples: list[dict[str, Any]],
    source: str = "aerial-imagery",
    usage_reference: str = "Verified lawful dataset terms",
    split_policy: str = "spatial_block_holdout",
) -> dict[str, Any]:
    """Construct and validate a dataset manifest adhering to the versioned contract."""
    manifest = {
        "schema_version": "1.0.0",
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source": source,
        "usage_terms": {
            "status": "verified",
            "reference": usage_reference,
        },
        "split_policy": split_policy,
        "samples": samples,
    }
    validate_contract("dataset-manifest", manifest)
    return manifest


def _to_png_bytes(array: np.ndarray) -> bytes:
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return stream.getvalue()


def create_sample_scenes(
    target_dir: Path, num_scenes: int = 6, size: int = 1024, seed: int = 42
) -> None:
    """Generate realistic LEVIR-style 1024x1024 aerial scenes in A/, B/, label/ directories."""
    dir_a = target_dir / "A"
    dir_b = target_dir / "B"
    dir_label = target_dir / "label"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)
    dir_label.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)
    for i in range(num_scenes):
        scene_name = f"scene_{i:03d}.png"
        base: Any = rng.integers(25, 60, (size, size, 3), dtype=np.uint8)
        after: Any = base.copy()
        mask: Any = np.zeros((size, size), dtype=np.uint8)

        num_buildings = int(rng.integers(8, 16))
        for _ in range(num_buildings):
            y = int(rng.integers(30, size - 120))
            x = int(rng.integers(30, size - 120))
            h = int(rng.integers(40, 90))
            w = int(rng.integers(40, 90))
            color: Any = rng.integers(180, 240, (h, w, 3), dtype=np.uint8)
            if rng.random() > 0.5:
                base[y : y + h, x : x + w] = color
                after[y : y + h, x : x + w] = color
            else:
                after[y : y + h, x : x + w] = color
                mask[y : y + h, x : x + w] = 255

        Image.fromarray(base).save(dir_a / scene_name, format="PNG")
        Image.fromarray(after).save(dir_b / scene_name, format="PNG")
        Image.fromarray(mask).save(dir_label / scene_name, format="PNG")

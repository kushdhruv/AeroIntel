import io
from pathlib import Path

import numpy as np
from PIL import Image

from contracts.validation import validate_contract
from modules.ingestion.tiler import build_dataset_manifest, tile_scene


def _make_dummy_image(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


def _make_dummy_mask(size: tuple[int, int]) -> bytes:
    stream = io.BytesIO()
    arr = np.zeros(size, dtype=np.uint8)
    arr[50:150, 50:150] = 255
    Image.fromarray(arr).save(stream, format="PNG")
    return stream.getvalue()


def test_tiler_slices_scene_and_builds_valid_manifest(tmp_path: Path) -> None:
    src_dir = tmp_path / "raw"
    src_dir.mkdir()
    before_file = src_dir / "scene01_before.png"
    after_file = src_dir / "scene01_after.png"
    mask_file = src_dir / "scene01_mask.png"

    # Create 512x512 scenes -> should produce 4 tiles of 256x256
    before_file.write_bytes(_make_dummy_image((512, 512), (30, 40, 50)))
    after_file.write_bytes(_make_dummy_image((512, 512), (200, 210, 220)))
    mask_file.write_bytes(_make_dummy_mask((512, 512)))

    out_dir = tmp_path / "tiles"
    samples = tile_scene(
        before_path=before_file,
        after_path=after_file,
        mask_path=mask_file,
        output_dir=out_dir,
        scene_id="scene01",
        region_id="region-north",
        crs="EPSG:4326",
        bounds=[-122.5, 37.7, -122.4, 37.8],
        tile_size=256,
    )

    assert len(samples) == 4
    for sample in samples:
        assert sample["source_scene_id"] == "scene01"
        assert sample["metadata"]["region_id"] == "region-north"
        assert sample["metadata"]["crs"] == "EPSG:4326"
        assert len(sample["metadata"]["bounds"]) == 4
        # Verify files exist on disk
        assert (out_dir / sample["image_pair"]["before"]).exists()
        assert (out_dir / sample["image_pair"]["after"]).exists()
        assert sample["mask"] is not None and (out_dir / sample["mask"]).exists()

    manifest = build_dataset_manifest(
        dataset_id="test-aerial",
        dataset_version="v1.0",
        samples=samples,
        source="levir-cd-subset",
        usage_reference="Verified research use terms",
    )

    # Validates contract
    validate_contract("dataset-manifest", manifest)
    assert manifest["dataset_id"] == "test-aerial"
    assert len(manifest["samples"]) == 4

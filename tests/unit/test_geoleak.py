import copy
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.errors import Invalid
from modules.geoleak.splits import leakage, make_split, overlaps
from modules.ingestion.synthetic import generate
from modules.propertypulse.training.engine import confusion_metrics


def test_splits_have_no_scene_tile_or_region_leakage(tmp_path: Path) -> None:
    samples = generate(tmp_path)["samples"]
    for method in ("scene", "spatial"):
        split = make_split(samples, method, 42)
        assert split == make_split(samples, method, 42)
        assert leakage(samples, split) == {
            "duplicate_pairs": 0,
            "scene_leaks": 0,
            "overlapping_tiles": 0,
            "overlap_check_status": "completed",
        }
        if method == "spatial":
            regions = [
                {s["metadata"]["region_id"] for s in samples if s["sample_id"] in split[fold]}
                for fold in ("train", "validation", "test")
            ]
            assert not regions[0] & regions[1] and not regions[0] & regions[2]


def test_duplicates_and_overlaps_across_split_detected(tmp_path: Path) -> None:
    samples = generate(tmp_path)["samples"][:2]
    samples[1]["checksum"] = copy.deepcopy(samples[0]["checksum"])
    samples[1]["metadata"]["bounds"] = samples[0]["metadata"]["bounds"]
    checks = leakage(
        samples,
        {"train": [samples[0]["sample_id"]], "validation": [], "test": [samples[1]["sample_id"]]},
    )
    assert checks["duplicate_pairs"] == checks["scene_leaks"] == checks["overlapping_tiles"] == 1


def test_no_geography_cannot_generate_spatial_holdout(tmp_path: Path) -> None:
    samples = generate(tmp_path)["samples"]
    for sample in samples:
        sample["metadata"]["crs"] = sample["metadata"]["bounds"] = None
    with pytest.raises(Invalid, match="Spatial holdout"):
        make_split(samples, "spatial", 42)
    assert (
        leakage(samples, make_split(samples, "scene", 42))["overlap_check_status"] == "unavailable"
    )


@given(st.integers(0, 10000), st.integers(0, 10000), st.integers(0, 10000))
def test_metrics_are_bounded_and_f1_matches_iou(tp: int, fp: int, fn: int) -> None:
    scores = confusion_metrics(tp, fp, fn)
    assert all(0 <= value <= 1 for value in scores.values())
    assert scores["f1"] == pytest.approx(2 * scores["iou"] / (1 + scores["iou"]))


@given(st.floats(-100, 100, allow_nan=False), st.floats(0.01, 100, allow_nan=False))
def test_overlap_is_symmetric(x: float, width: float) -> None:
    a, b = (x, 0, x + width, 10), (0, 0, 5, 5)
    assert overlaps(a, b) == overlaps(b, a)


def test_empty_masks_metrics() -> None:
    assert confusion_metrics(0, 0, 0) == {"iou": 1, "f1": 1, "precision": 1, "recall": 1}
    assert confusion_metrics(0, 4, 0)["iou"] == 0

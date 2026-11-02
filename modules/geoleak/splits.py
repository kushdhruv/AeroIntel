"""Freeze independent split assignments before creating training patches."""

import math
import random
from typing import Any

from app.core.errors import Invalid


def geographic_bounds(sample: dict[str, Any]) -> tuple[float, float, float, float] | None:
    metadata = sample["metadata"]
    if metadata["crs"] is None or metadata["bounds"] is None:
        return None
    from pyproj import Transformer

    transformer = Transformer.from_crs(metadata["crs"], "EPSG:6933", always_xy=True)
    # Densification conservatively encloses non-linear projected footprints.
    bounds = transformer.transform_bounds(*metadata["bounds"], densify_pts=21)
    if not all(math.isfinite(x) for x in bounds) or bounds[0] >= bounds[2]:
        raise Invalid("Unsupported footprint, including antimeridian crossing")
    return tuple(bounds)


def overlaps(a: tuple[float, ...], b: tuple[float, ...], buffer: float = 0) -> bool:
    return (
        a[0] < b[2] + buffer
        and b[0] < a[2] + buffer
        and (a[1] < b[3] + buffer and b[1] < a[3] + buffer)
    )


def make_split(
    samples: list[dict[str, Any]], method: str, seed: int, block_meters: float = 10_000
) -> dict[str, list[str]]:
    if method not in {"random", "scene", "spatial"} or block_meters <= 0:
        raise Invalid("Invalid split configuration")
    bounds = [geographic_bounds(sample) for sample in samples]
    if method == "spatial" and (
        not all(bounds) or not all(sample["metadata"]["region_id"] for sample in samples)
    ):
        raise Invalid("Spatial holdout requires validated footprints and region identities")
    parent = list(range(len(samples)))

    def root(index: int) -> int:
        while parent[index] != index:
            index = parent[index]
        return index

    for i, left in enumerate(samples):
        for j in range(i):
            right = samples[j]
            related = method != "random" and left["source_scene_id"] == right["source_scene_id"]
            left_bounds, right_bounds = bounds[i], bounds[j]
            if method != "random" and left_bounds is not None and right_bounds is not None:
                related |= overlaps(left_bounds, right_bounds, buffer=10.0)
            if method == "spatial":
                a, b = bounds[i], bounds[j]
                assert a is not None and b is not None
                related |= left["metadata"]["region_id"] == right["metadata"]["region_id"]
                # Footprints sharing any grid block must remain in the same group.
                related |= (
                    math.floor(a[0] / block_meters) <= math.floor(b[2] / block_meters)
                    and math.floor(b[0] / block_meters) <= math.floor(a[2] / block_meters)
                    and math.floor(a[1] / block_meters) <= math.floor(b[3] / block_meters)
                    and math.floor(b[1] / block_meters) <= math.floor(a[3] / block_meters)
                )
            if related:
                parent[root(i)] = root(j)
    groups: dict[int, list[str]] = {}
    for index, sample in enumerate(samples):
        groups.setdefault(root(index), []).append(sample["sample_id"])
    ordered = sorted(groups.values(), key=lambda group: sorted(group)[0])
    random.Random(seed).shuffle(ordered)
    if len(ordered) < 3:
        raise Invalid(f"{method} split requires at least three independent groups")
    count = max(1, len(ordered) // 5)
    parts = {
        "test": ordered[:count],
        "validation": ordered[count : 2 * count],
        "train": ordered[2 * count :],
    }
    return {
        name: sorted(item for group in values for item in group) for name, values in parts.items()
    }


def leakage(samples: list[dict[str, Any]], split: dict[str, list[str]]) -> dict[str, Any]:
    assignments = {sample_id: fold for fold, ids in split.items() for sample_id in ids}
    expected = {sample["sample_id"] for sample in samples}
    if set(assignments) != expected or sum(map(len, split.values())) != len(expected):
        raise Invalid("Split must assign each sample exactly once")
    duplicates = scenes = tiles = 0
    available = True
    for i, a in enumerate(samples):
        a_bounds = geographic_bounds(a)
        available &= a_bounds is not None
        for b in samples[:i]:
            if assignments[a["sample_id"]] == assignments[b["sample_id"]]:
                continue
            duplicates += int(
                bool(
                    {a["checksum"]["before"], a["checksum"]["after"]}
                    & {b["checksum"]["before"], b["checksum"]["after"]}
                )
            )
            scenes += int(a["source_scene_id"] == b["source_scene_id"])
            b_bounds = geographic_bounds(b)
            if a_bounds and b_bounds:
                tiles += int(overlaps(a_bounds, b_bounds))
    return {
        "duplicate_pairs": duplicates,
        "scene_leaks": scenes,
        "overlapping_tiles": tiles,
        "overlap_check_status": "completed" if available else "unavailable",
    }

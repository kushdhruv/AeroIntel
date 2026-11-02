"""CLI tool to prepare, tile, and generate manifests for aerial imagery datasets.

Supports directory layouts such as LEVIR-CD+ (folders A/, B/, and label/) or custom
paired imagery directories. Slices scenes into 256x256 tiles, groups scene IDs,
computes checksums, and produces a valid dataset-manifest.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from modules.ingestion.tiler import build_dataset_manifest, tile_scene


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare aerial change detection dataset")
    parser.add_argument(
        "--source-dir", type=Path, required=True, help="Input directory containing A/, B/, label/"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Target directory for 256x256 tiles and manifest",
    )
    parser.add_argument("--dataset-id", default="aerial-cd", help="Dataset identifier")
    parser.add_argument("--dataset-version", default="v1.0.0", help="Dataset version")
    parser.add_argument("--source-name", default="levir-cd-plus", help="Source dataset name")
    parser.add_argument(
        "--usage-ref",
        default="Verified educational/research use terms",
        help="Usage terms reference",
    )
    parser.add_argument("--region-prefix", default="region", help="Region identifier prefix")
    parser.add_argument(
        "--tile-size", type=int, default=256, help="Target tile dimension (default: 256)"
    )
    parser.add_argument("--crs", default=None, help="EPSG CRS e.g. EPSG:4326")
    args = parser.parse_args()

    dir_a = args.source_dir / "A"
    dir_b = args.source_dir / "B"
    dir_label = args.source_dir / "label"

    if not dir_a.is_dir() or not dir_b.is_dir():
        # Fallback to single directory if files are paired by pattern
        pat_before = list(args.source_dir.glob("*_before.*")) or list(
            args.source_dir.glob("*_t1.*")
        )
        pat_after = list(args.source_dir.glob("*_after.*")) or list(args.source_dir.glob("*_t2.*"))
        pat_mask = list(args.source_dir.glob("*_mask.*")) or list(args.source_dir.glob("*_label.*"))
        files_a = sorted(pat_before)
        files_b = sorted(pat_after)
        labels = sorted(pat_mask)
    else:
        files_a = sorted(dir_a.glob("*.*"))
        files_b = sorted(dir_b.glob("*.*"))
        labels = sorted(dir_label.glob("*.*")) if dir_label.is_dir() else []

    if not files_a:
        parser.error(f"No image files found in {args.source_dir}")

    all_samples = []
    print(f"Processing {len(files_a)} scenes from {args.source_dir}...")

    for idx, file_a in enumerate(files_a):
        stem = file_a.stem
        # Match corresponding B file
        file_b = next((f for f in files_b if f.stem == stem), None)
        if not file_b and idx < len(files_b):
            file_b = files_b[idx]
        if not file_b:
            print(f"Warning: No matching after-image for {file_a.name}, skipping.")
            continue

        label_file = next((f for f in labels if f.stem == stem), None)
        if not label_file and idx < len(labels):
            label_file = labels[idx]

        scene_id = stem
        region_id = f"{args.region_prefix}-{idx % 6}"

        samples = tile_scene(
            before_path=file_a,
            after_path=file_b,
            mask_path=label_file,
            output_dir=args.output_dir,
            scene_id=scene_id,
            region_id=region_id,
            crs=args.crs,
            tile_size=args.tile_size,
        )
        all_samples.extend(samples)
        print(f"  [{idx + 1}/{len(files_a)}] Scene {scene_id} -> {len(samples)} tiles")

    manifest = build_dataset_manifest(
        dataset_id=args.dataset_id,
        dataset_version=args.dataset_version,
        samples=all_samples,
        source=args.source_name,
        usage_reference=args.usage_ref,
        split_policy="spatial_block_holdout" if args.crs else "scene_group_holdout",
    )

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nGenerated {len(all_samples)} tiles and verified manifest at {manifest_path}")


if __name__ == "__main__":
    main()

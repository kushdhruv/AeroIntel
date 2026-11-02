"""Validate and snapshot data before committing a dataset-completed event."""

import copy
import io
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from adapters.storage.local import LocalStorage, digest
from app.core.database import Database, encode
from app.core.errors import Invalid
from app.core.geospatial import configure_rasterio
from contracts.validation import validate_contract

MAX_PIXELS = 16_777_216
configure_rasterio()


def decode_image(data: bytes, mask: bool = False) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS or min(image.size) < 8:
                raise Invalid("Image dimensions exceed limits or are too small")
            image.load()
            if mask:
                array = np.asarray(image)
                if array.ndim != 2 or not set(np.unique(array)).issubset({0, 1, 255}):
                    raise Invalid("Masks must be single-channel binary 0/1 or 0/255")
                return (array > 0).astype(np.float32)
            if image.mode != "RGB":
                raise Invalid("Images must have three RGB channels")
            return np.asarray(image, dtype=np.float32) / 255.0
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise Invalid("Invalid or corrupted image") from exc


def validate_metadata(metadata: dict[str, Any]) -> None:
    before, after = metadata.get("acquisition_before"), metadata.get("acquisition_after")
    try:
        if before and after and date.fromisoformat(before) >= date.fromisoformat(after):
            raise Invalid("Before acquisition must precede after acquisition")
    except (TypeError, ValueError) as exc:
        raise Invalid("Acquisition dates must be ISO dates") from exc
    if (metadata.get("crs") is None) != (metadata.get("bounds") is None):
        raise Invalid("CRS and bounds must be supplied together")
    if metadata.get("crs") is not None:
        from pyproj import CRS

        try:
            crs = CRS.from_user_input(metadata["crs"])
        except Exception as exc:
            raise Invalid("Unrecognized CRS") from exc
        bounds = metadata["bounds"]
        if (
            not isinstance(bounds, list)
            or len(bounds) != 4
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) for value in bounds
            )
        ):
            raise Invalid("Bounds must contain four finite numbers")
        if not np.isfinite(bounds).all() or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise Invalid("Invalid footprint bounds")
        if crs.is_geographic and not (
            -180 <= bounds[0] < bounds[2] <= 180 and -90 <= bounds[1] < bounds[3] <= 90
        ):
            raise Invalid("Geographic footprint is outside longitude/latitude bounds")


def raster_metadata(raw: bytes) -> tuple[Any, Any, Any] | None:
    if raw[:4] not in {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}:
        return None
    from rasterio.io import MemoryFile

    with MemoryFile(raw) as memory, memory.open() as raster:
        if (
            raster.transform.b != 0
            or raster.transform.d != 0
            or (raster.crs is not None and (raster.transform.a <= 0 or raster.transform.e >= 0))
        ):
            raise Invalid("Rotated/sheared TIFFs must be reprojected to a north-up grid")
        return raster.crs, raster.transform, tuple(raster.bounds)


def validate_inference_georeferencing(
    before: bytes, after: bytes, metadata: dict[str, Any] | None
) -> None:
    first, second = raster_metadata(before), raster_metadata(after)
    if first is not None and second is not None and first[:2] != second[:2]:
        raise Invalid("TIFF pair CRS/transforms differ; align before inference")
    if (first is not None and first[0] is not None) != (
        second is not None and second[0] is not None
    ):
        raise Invalid("Both dates must supply consistent georeferencing")
    if first is not None and first[0] is not None and metadata and metadata.get("crs"):
        if metadata["crs"] != first[0].to_string() or not np.allclose(metadata["bounds"], first[2]):
            raise Invalid("Inference footprint differs from GeoTIFF metadata")


class Ingestion:
    def __init__(self, database: Database, storage: LocalStorage) -> None:
        self.db, self.storage = database, storage

    def register(self, manifest: dict[str, Any], source_root: Path) -> dict[str, Any]:
        validate_contract("dataset-manifest", manifest)
        terms = manifest["usage_terms"]
        if terms["status"] != "verified" or not terms["reference"]:
            raise Invalid("Verified usage terms and their reference are required")
        document = copy.deepcopy(manifest)
        seen = set()
        seen_pairs = set()
        source = LocalStorage(source_root)
        for sample in document["samples"]:
            if sample["sample_id"] in seen:
                raise Invalid("Duplicate sample_id")
            seen.add(sample["sample_id"])
            pair = (sample["checksum"]["before"], sample["checksum"]["after"])
            if pair in seen_pairs:
                raise Invalid("Duplicate image pair under different sample IDs")
            seen_pairs.add(pair)
            validate_metadata(sample["metadata"])
            arrays = []
            rasters = []
            for field in ("before", "after", "mask"):
                key = sample["mask"] if field == "mask" else sample["image_pair"][field]
                if not key or not sample["checksum"][field]:
                    raise Invalid("Training datasets require images, masks and checksums")
                raw = source.read(key, sample["checksum"][field])
                arrays.append(decode_image(raw, mask=field == "mask"))
                rasters.append(raster_metadata(raw))
                stored = f"datasets/objects/{digest(raw)[7:]}"
                self.storage.write(stored, raw)
                if field == "mask":
                    sample["mask"] = stored
                else:
                    sample["image_pair"][field] = stored
            if len({array.shape[:2] for array in arrays}) != 1:
                raise Invalid("Image pair and mask must have matching dimensions; align upstream")
            first, second = rasters[:2]
            if first is not None and second is not None:
                if first[:2] != second[:2]:
                    raise Invalid("TIFF pair CRS/transforms differ; align before ingestion")
                if first[0] is not None:
                    if sample["metadata"]["crs"] != first[0].to_string() or not np.allclose(
                        sample["metadata"]["bounds"], first[2], rtol=1e-8, atol=1e-8
                    ):
                        raise Invalid("GeoTIFF metadata differs from manifest")
            if rasters[2] is not None and first is not None and rasters[2][:2] != first[:2]:
                raise Invalid("Mask georeferencing differs from imagery")
        key = f"{manifest['dataset_id']}:{manifest['dataset_version']}"
        fingerprint = digest(encode(manifest).encode())
        with self.db.transaction() as db:
            previous = db.execute(
                "SELECT fingerprint FROM dedup WHERE scope='ingest' AND key=?", (key,)
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    from app.core.errors import Conflict

                    raise Conflict("Dataset version is immutable")
                return self.db.get("dataset", key, db)
            self.db.put(db, "dataset", key, document)
            manifest_key = f"datasets/manifests/{digest(key.encode())[7:]}.json"
            raw_manifest = encode(document).encode()
            self.storage.write(manifest_key, raw_manifest)
            db.execute(
                "INSERT INTO dedup VALUES('ingest',?,?,?)", (key, fingerprint, encode(document))
            )
            self.db.event(
                db,
                "dataset-batch-completed",
                {
                    "dataset_id": document["dataset_id"],
                    "dataset_version": document["dataset_version"],
                    "manifest_uri": manifest_key,
                    "manifest_sha256": digest(raw_manifest),
                },
                key,
                "ingestion",
            )
            self.db.audit("ingestion", "register_dataset", key, "allowed", fingerprint, db=db)
        return document

    def verify(self, manifest: dict[str, Any]) -> None:
        for sample in manifest["samples"]:
            for field in ("before", "after", "mask"):
                key = sample["mask"] if field == "mask" else sample["image_pair"][field]
                self.storage.read(key, sample["checksum"][field])

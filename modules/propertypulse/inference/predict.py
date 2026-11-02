import math
from typing import Any

import numpy as np
import torch

from app.core.errors import Invalid
from modules.propertypulse.models.siamese import SiameseUNet


@torch.no_grad()
def predict(
    model: SiameseUNet,
    before: np.ndarray,
    after: np.ndarray,
    threshold: float = 0.5,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if before.shape != after.shape or before.ndim != 3 or before.shape[2] != 3:
        raise Invalid("Aligned RGB image pairs are required")
    if not np.isfinite(before).all() or not np.isfinite(after).all():
        raise Invalid("Non-finite input pixels")
    if min(before.min(), after.min()) < 0 or max(before.max(), after.max()) > 1:
        raise Invalid("Input must be normalized to [0,1]")
    height, width = before.shape[:2]
    probabilities = np.zeros((height, width), dtype=np.float32)
    model.eval()
    for y in range(0, height, 256):
        for x in range(0, width, 256):
            h, w = min(256, height - y), min(256, width - x)
            tensors = []
            for image in (before, after):
                tile: Any = np.zeros((256, 256, 3), dtype=np.float32)
                tile[:h, :w] = image[y : y + h, x : x + w]
                tensors.append(torch.from_numpy(tile.transpose(2, 0, 1).copy()).unsqueeze(0))
            probabilities[y : y + h, x : x + w] = model(*tensors).sigmoid()[0, 0, :h, :w].numpy()
    mask = probabilities >= threshold
    clipped = np.clip(probabilities, 1e-6, 1 - 1e-6)
    entropy = float(
        np.mean(-(clipped * np.log(clipped) + (1 - clipped) * np.log(1 - clipped))) / math.log(2)
    )
    polygons = None
    if metadata and metadata.get("crs") and metadata.get("bounds"):
        from rasterio.features import shapes
        from rasterio.transform import from_bounds
        from rasterio.warp import transform_geom

        transform = from_bounds(*metadata["bounds"], width, height)
        features = [
            {
                "type": "Feature",
                "properties": {},
                "geometry": transform_geom(
                    metadata["crs"],
                    "EPSG:4326",
                    geometry,
                ),
            }
            for geometry, value in shapes(mask.astype("uint8"), mask=mask, transform=transform)
            if value == 1
        ]
        polygons = {"type": "FeatureCollection", "features": features}
    return {
        "probability_mask": probabilities,
        "mask": mask.astype("uint8"),
        "change_percentage": float(mask.mean() * 100),
        "uncertainty": entropy,
        "confidence": 1 - entropy,
        "review_required": entropy > 0.35,
        "geojson": polygons,
    }

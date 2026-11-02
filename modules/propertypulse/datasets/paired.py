from typing import Any

import numpy as np
import torch
from torch.utils.data import Dataset

from adapters.storage.local import LocalStorage
from modules.ingestion.service import decode_image


class PairedTiles(Dataset):
    """No resampling: aligned pairs are padded and tiled identically at both dates."""

    def __init__(
        self, samples: list[dict[str, Any]], storage: LocalStorage, size: int = 256
    ) -> None:
        self.tiles: list[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]] = []
        for sample in samples:
            before = decode_image(
                storage.read(sample["image_pair"]["before"], sample["checksum"]["before"])
            )
            after = decode_image(
                storage.read(sample["image_pair"]["after"], sample["checksum"]["after"])
            )
            mask = decode_image(storage.read(sample["mask"], sample["checksum"]["mask"]), True)
            height, width = mask.shape
            for y in range(0, height, size):
                for x in range(0, width, size):
                    h, w = min(size, height - y), min(size, width - x)
                    images = []
                    for image in (before, after):
                        tile: Any = np.zeros((size, size, 3), dtype=np.float32)
                        tile[:h, :w] = image[y : y + h, x : x + w]
                        images.append(torch.from_numpy(tile.transpose(2, 0, 1).copy()))
                    target: Any = np.zeros((1, size, size), dtype=np.float32)
                    valid = np.zeros_like(target)
                    target[0, :h, :w] = mask[y : y + h, x : x + w]
                    valid[0, :h, :w] = 1
                    self.tiles.append((*images, torch.from_numpy(target), torch.from_numpy(valid)))

    def __len__(self) -> int:
        return len(self.tiles)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, ...]:
        return self.tiles[index]

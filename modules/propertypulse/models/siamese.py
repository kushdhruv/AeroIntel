"""Small shared-encoder Siamese U-Net, trained from scratch without downloaded weights.

Independent implementation of the blueprint's absolute-difference/skip pattern;
no unlicensed reference source or checkpoint is incorporated.
"""

import torch
from torch import nn
from torch.nn import functional as F


def block(inputs: int, outputs: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(inputs, outputs, 3, padding=1),
        nn.ReLU(),
        nn.Conv2d(outputs, outputs, 3, padding=1),
        nn.ReLU(),
    )


class SiameseUNet(nn.Module):
    def __init__(self, width: int = 4) -> None:
        super().__init__()
        self.enc1 = block(3, width)
        self.enc2 = block(width, width * 2)
        self.bridge = block(width * 2, width * 4)
        self.dec2 = block(width * 6, width * 2)
        self.dec1 = block(width * 3 + 3, width)
        self.head = nn.Conv2d(width, 1, 1)

    def encode(self, image: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        first = self.enc1(image)
        second = self.enc2(F.max_pool2d(first, 2))
        return first, second, self.bridge(F.max_pool2d(second, 2))

    def forward(self, before: torch.Tensor, after: torch.Tensor) -> torch.Tensor:
        a, b = self.encode(before), self.encode(after)
        differences = [torch.abs(x - y) for x, y in zip(a, b, strict=True)]
        decoded = self.dec2(
            torch.cat(
                [
                    F.interpolate(differences[2], size=differences[1].shape[-2:], mode="nearest"),
                    differences[1],
                ],
                dim=1,
            )
        )
        decoded = self.dec1(
            torch.cat(
                [
                    F.interpolate(decoded, size=differences[0].shape[-2:], mode="nearest"),
                    differences[0],
                    torch.abs(before - after),
                ],
                dim=1,
            )
        )
        return self.head(decoded)

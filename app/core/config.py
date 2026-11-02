from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    contract_version: Literal["1.0.0"] = "1.0.0"
    execution_mode: Literal["local", "aws"] = "local"
    home: Path = Path("data/platform")
    quality_policy_path: Path | None = None
    patch_size: Literal[256] = 256
    cloud_execution_enabled: bool = False


def load_settings(path: Path = Path("configs/local.yaml")) -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

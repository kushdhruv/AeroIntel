"""Composition root. Domain modules receive explicit local dependencies."""

import os
from pathlib import Path
from typing import Any

from adapters.storage.local import LocalStorage
from app.core.config import Settings, load_settings
from app.core.database import Database
from app.core.errors import Invalid
from modules.geoleak.gates import load_policy
from modules.geoshift.service import Monitoring, Production
from modules.ingestion.service import Ingestion
from modules.pipeline.engine import Pipeline
from modules.registry.deployment import LocalDeployment
from modules.registry.service import Registry


class Platform:
    def __init__(self, root: Path | None = None, policy: dict[str, Any] | None = None) -> None:
        config_path = os.environ.get("AEROINTEL_CONFIG")
        self.settings = load_settings(Path(config_path)) if config_path else Settings()
        if self.settings.execution_mode != "local" or self.settings.cloud_execution_enabled:
            raise Invalid(
                "Use explicit AWS adapters for cloud execution; local platform never provisions AWS"
            )
        self.root = (
            root or Path(os.environ.get("AEROINTEL_HOME", str(self.settings.home)))
        ).resolve()
        self.storage = LocalStorage(self.root / "objects")
        self.db = Database(self.root / "metadata.db")
        self.registry = Registry(
            self.db, self.storage, policy or load_policy(self.settings.quality_policy_path)
        )
        self.ingestion = Ingestion(self.db, self.storage)
        self.pipeline = Pipeline(self.db, self.storage, self.registry)
        self.deployment = LocalDeployment(self.registry)
        self.production = Production(self.deployment)
        self.monitoring = Monitoring(self.production)

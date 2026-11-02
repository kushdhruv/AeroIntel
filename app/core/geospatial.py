"""Use the wheel's matched GDAL/PROJ data, not an unrelated host GIS install."""

import os
from pathlib import Path


def configure_rasterio() -> None:
    import rasterio
    from rasterio.env import set_proj_data_search_path

    bundled = Path(rasterio.__file__).parent / "proj_data"
    if (bundled / "proj.db").is_file():
        # Env.start reads these on each new GDAL environment, so set process-local
        # values as well as the current context. Never change machine/user settings.
        os.environ["PROJ_DATA"] = str(bundled)
        os.environ["PROJ_LIB"] = str(bundled)
        # Rasterio exposes this helper through env; keep the compatibility seam here.
        # PyProj maintains its own context and bundled database independently.
        set_proj_data_search_path(str(bundled))

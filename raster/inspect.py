"""用 rasterio 打开并读取 GeoTIFF 基本信息。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import rasterio

from utils.logging import get_logger

logger = get_logger(__name__)


class RasterInspectError(RuntimeError):
    """栅格无法打开或读取。"""


@dataclass
class RasterInfo:
    path: str
    exists: bool = False
    readable: bool = False
    width: int = 0
    height: int = 0
    bands: int = 0
    crs: str = ""
    resolution: str = ""
    dtype: str = ""
    nodata: Optional[float] = None
    transform: Optional[list] = None
    bounds: Optional[list] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "exists": self.exists,
            "readable": self.readable,
            "width": self.width,
            "height": self.height,
            "bands": self.bands,
            "crs": self.crs,
            "resolution": self.resolution,
            "dtype": self.dtype,
            "nodata": self.nodata,
            "transform": self.transform,
            "bounds": self.bounds,
            "error": self.error,
        }


def inspect_raster(path: str | Path) -> RasterInfo:
    """打开栅格并读取信息；失败时 readable=False 并记录 error。"""
    p = Path(path)
    info = RasterInfo(path=str(p))
    if not p.exists():
        info.error = "File does not exist"
        return info
    info.exists = True
    try:
        with rasterio.open(str(p)) as src:
            info.readable = True
            info.width = src.width
            info.height = src.height
            info.bands = src.count
            info.crs = str(src.crs or "")
            res = src.res
            info.resolution = f"{res[0]:.6g} × {res[1]:.6g}"
            info.dtype = src.dtypes[0] if src.dtypes else ""
            info.nodata = src.nodata
            info.transform = list(src.transform)[:6]
            b = src.bounds
            info.bounds = [b.left, b.bottom, b.right, b.top]
    except Exception as exc:  # noqa: BLE001
        info.error = f"Raster read failed: {exc}"
        logger.warning("栅格读取失败 %s: %s", p, exc)
    return info

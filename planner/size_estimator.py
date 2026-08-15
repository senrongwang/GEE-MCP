"""下载规模估算（设计文档第 17 节：Estimate Size）。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import ee

from utils.logging import get_logger

logger = get_logger(__name__)

# 各 dtype 每像素字节数（近似）
_DTYPE_BYTES = {
    "INT8": 1, "UINT8": 1, "BYTE": 1,
    "INT16": 2, "UINT16": 2, "SHORT": 2,
    "INT32": 4, "UINT32": 4, "INT": 4,
    "FLOAT": 4, "FLOAT32": 4,
    "DOUBLE": 8, "FLOAT64": 8, "FLOAT_64": 8,
}


@dataclass
class SizeEstimate:
    pixel_count: int
    grid_dimension: int  # sqrt(pixel_count)，判断是否超过 GEE 限制
    bytes_total: int
    mb_total: float
    bytes_per_band: int
    dtype: str = "FLOAT32"
    band_count: int = 1

    def to_dict(self) -> dict:
        return {
            "pixel_count": self.pixel_count,
            "grid_dimension": self.grid_dimension,
            "bytes_total": self.bytes_total,
            "mb_total": round(self.mb_total, 2),
            "dtype": self.dtype,
            "band_count": self.band_count,
        }


def estimate_pixels(region_area_m2: float, scale: int) -> int:
    """按区域面积与分辨率估算像元数。"""
    if scale <= 0:
        return 0
    return max(1, int(round(region_area_m2 / (scale * scale))))


def estimate_grid_dimension(pixel_count: int) -> int:
    return max(1, int(math.ceil(math.sqrt(pixel_count))))


def estimate_raster_size(
    region_area_m2: float,
    scale: int,
    band_count: int = 1,
    dtype: str = "FLOAT32",
) -> SizeEstimate:
    """估算单景输出的体量（未压缩，实际 GeoTIFF 更小）。"""
    pixels = estimate_pixels(region_area_m2, scale)
    bytes_per_band = pixels * _DTYPE_BYTES.get(dtype.upper(), 4)
    total = bytes_per_band * max(1, band_count)
    return SizeEstimate(
        pixel_count=pixels,
        grid_dimension=estimate_grid_dimension(pixels),
        bytes_total=total,
        mb_total=total / (1024 * 1024),
        bytes_per_band=bytes_per_band,
        dtype=dtype,
        band_count=max(1, band_count),
    )


def region_area_m2(region: ee.Geometry) -> float:
    """获取 region 面积（平方米，geodesic）。"""
    try:
        area = region.area(1).getInfo()
        return float(area)
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法获取 region 面积: %s", exc)
        # 兜底：用 bounds 的粗略面积
        try:
            b = region.bounds().getInfo().get("coordinates", [[[0, 0], [0, 0], [0, 0], [0, 0]]])
            xs = [p[0] for p in b[0]]
            ys = [p[1] for p in b[0]]
            lon = max(xs) - min(xs)
            lat = max(ys) - min(ys)
            return abs(lon * lat * 111_320.0 * 111_320.0)
        except Exception:  # noqa: BLE001
            return 0.0

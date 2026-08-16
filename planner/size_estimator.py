"""下载规模估算（设计文档第 17 节：Estimate Size）。

P0-2 修复：规模估算从「geodesic 球面面积」改为「目标 CRS 下的实际网格」。
Web Mercator（EPSG:3857）存在纬度拉伸（~1/cos(φ)），同一区域在 3857 网格下的
像元数明显多于球面面积估算（中国全域约 1.4 倍），若用球面面积估算会低估请求体量，
导致 dry_run「看起来安全」、实际执行时单块请求超限失败。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import ee

from utils.logging import get_logger

logger = get_logger(__name__)

# 各 dtype 每像素字节数（近似）
DTYPE_BYTES = {
    "INT8": 1, "UINT8": 1, "BYTE": 1,
    "INT16": 2, "UINT16": 2, "SHORT": 2,
    "INT32": 4, "UINT32": 4, "INT": 4,
    "FLOAT": 4, "FLOAT32": 4,
    "DOUBLE": 8, "FLOAT64": 8, "FLOAT_64": 8,
}

# GEE getDownloadURL 实际上限：50331648 字节（48 MiB）
GEE_REQUEST_LIMIT_BYTES = 48 * 1024 * 1024
# 保守请求预算：留 ~4MiB 给请求头 / GeoTIFF 元数据等开销
DEFAULT_MAX_REQUEST_BYTES = 44 * 1024 * 1024
# GEE 服务端把计算结果渲染为 float64 后打包，请求大小按 8 字节/像素计算；
# 未知 dtype 一律按 float64 保守处理（这是 P0-1 根因：8M 像素 × 8B = 64MB > 上限）
DEFAULT_REQUEST_BYTES_PER_PIXEL = 8


def bytes_per_pixel(dtype: str = "FLOAT64") -> int:
    """dtype 每像素字节数；未知类型按 float64（8B）保守处理。"""
    return DTYPE_BYTES.get(dtype.upper(), DEFAULT_REQUEST_BYTES_PER_PIXEL)


def capped_request_budget(max_request_bytes: Optional[int] = None) -> int:
    """请求字节预算，硬性封顶在 GEE 实际上限 48MiB（防止配置超限）。"""
    budget = max_request_bytes or DEFAULT_MAX_REQUEST_BYTES
    return min(int(budget), GEE_REQUEST_LIMIT_BYTES)


def max_chunk_pixels_for_dtype(dtype: str = "FLOAT64",
                               max_request_bytes: Optional[int] = None) -> int:
    """按输出 dtype 反推单次 getDownloadURL 请求允许的最大像素数。

    GEE 对请求大小按 float64（8B/px）计算，因此默认按 FLOAT64 取保守值
    （约 44MiB / 8B ≈ 5.6M 像素），避免 8M×8B=64MB 超限失败。
    """
    budget = capped_request_budget(max_request_bytes)
    return max(1, int(budget // bytes_per_pixel(dtype)))


def estimate_request_bytes(width_px: int, height_px: int,
                           dtype: str = "FLOAT64") -> int:
    """估算 GEE 对 width_px×height_px 网格收取的请求字节数（float64 假设）。"""
    return max(1, width_px) * max(1, height_px) * bytes_per_pixel(dtype)


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
    """按区域面积与分辨率估算像元数（geodesic 粗略值，见 estimate_raster_size_grid）。"""
    if scale <= 0:
        return 0
    return max(1, int(round(region_area_m2 / (scale * scale))))


def estimate_grid_dimension(pixel_count: int) -> int:
    return max(1, int(math.ceil(math.sqrt(pixel_count))))


def _size_from_pixels(pixels: int, band_count: int = 1, dtype: str = "FLOAT64") -> SizeEstimate:
    bytes_per_band = pixels * bytes_per_pixel(dtype)
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


def estimate_raster_size(
    region_area_m2: float,
    scale: int,
    band_count: int = 1,
    dtype: str = "FLOAT64",
) -> SizeEstimate:
    """按 geodesic 面积估算单景输出体量（未压缩）。

    注意：GEE 返回 float64，估算请用 dtype="FLOAT64"（默认）；仅当确认输出
    会被转换为更小 dtype 时才传对应 dtype。EPSG:3857 等投影网格请优先使用
    estimate_raster_size_grid（P0-2）。
    """
    return _size_from_pixels(estimate_pixels(region_area_m2, scale), band_count, dtype)


def aligned_grid_bounds(region: ee.Geometry, scale: int, crs: str) -> Optional[dict]:
    """region 在目标 CRS 下的对齐网格（与 plan_chunks 同一套算法，P0-2）。

    返回 {x0, y0, x1, y1, width_px, height_px}（原点对齐到 scale 的整数倍）；
    计算失败返回 None。仅适用于投影坐标系（EPSG:3857 等）；
    EPSG:4326 是度数网格，除以米制 scale 无意义，请走 area 估算。
    """
    if scale <= 0:
        return None
    try:
        bounds = region.bounds(1, crs).coordinates().getInfo()
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法计算 region 在 %s 下的外包矩形: %s", crs, exc)
        return None
    xs = [p[0] for p in bounds[0]]
    ys = [p[1] for p in bounds[0]]
    x0 = math.floor(min(xs) / scale) * scale
    y0 = math.floor(min(ys) / scale) * scale
    x1 = math.ceil(max(xs) / scale) * scale
    y1 = math.ceil(max(ys) / scale) * scale
    return {
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "width_px": max(1, round((x1 - x0) / scale)),
        "height_px": max(1, round((y1 - y0) / scale)),
    }


def region_grid_pixels(region: ee.Geometry, scale: int, crs: str) -> int:
    """region 在目标 CRS 下、按 scale 对齐网格的像元总数（P0-2 核心）。

    与 plan_chunks 的分块口径一致，保证 dry_run 的 estimated_pixels 与实际一致。
    """
    g = aligned_grid_bounds(region, scale, crs)
    if not g:
        return 0
    return g["width_px"] * g["height_px"]


def estimate_raster_size_grid(
    region: ee.Geometry,
    scale: int,
    band_count: int = 1,
    dtype: str = "FLOAT64",
    crs: str = "EPSG:3857",
) -> SizeEstimate:
    """按目标 CRS 下的实际网格估算单景输出体量（P0-2，优先使用）。

    EPSG:4326 或 bounds 计算失败时回退到 geodesic 面积估算。
    """
    pixels = region_grid_pixels(region, scale, crs)
    if pixels <= 0:
        return estimate_raster_size(region_area_m2(region), scale, band_count, dtype)
    return _size_from_pixels(pixels, band_count, dtype)


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

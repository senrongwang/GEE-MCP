"""Direct Download 引擎：Image.getDownloadURL()（设计文档第 18 节）。

GEE 将 getDownloadURL 定位为小块 Image 数据下载接口（请求上限约 48MB）；
本引擎支持：
1. 逐 band 请求避免多 band zip；
2. 当 region 外包矩形网格过大时自动按矩形网格分片下载，再用 rasterio 拼接；
3. 多波段用 rasterio 合成多波段 GeoTIFF。
"""

from __future__ import annotations

import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import ee
import httpx
import rasterio
from rasterio.merge import merge as rio_merge

from config import Config
from utils.logging import get_logger

logger = get_logger(__name__)

# 单次 getDownloadURL 请求上限（GEE 限制约 50331648 字节；保守取 40MB）
_MAX_REQUEST_BYTES = 40 * 1024 * 1024
# 分片时每块像素上限（float32 每像素 4B）
_DEFAULT_MAX_CHUNK_PIXELS = 8_000_000


class DirectDownloadError(RuntimeError):
    """直接下载失败。"""


def download_url_to_file(
    url: str,
    out_path: str | Path,
    timeout_s: float = 300.0,
    retries: int = 3,
) -> Path:
    """流式下载 URL 到本地文件，带重试。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.stream("GET", url, timeout=timeout_s, follow_redirects=True) as resp:
                resp.raise_for_status()
                tmp = out.with_suffix(out.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1 << 20):
                        f.write(chunk)
            tmp.replace(out)
            return out
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("下载失败（第 %s/%s 次）: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(2 * attempt)
    raise DirectDownloadError(f"下载失败（已重试 {retries} 次）: {last_err}")


# ---------------------------------------------------------------- 分片规划
def plan_chunks(
    region: ee.Geometry,
    scale: int,
    crs: str,
    max_chunk_pixels: int = _DEFAULT_MAX_CHUNK_PIXELS,
) -> list[ee.Geometry]:
    """把 region 的外包矩形按网格切成若干子矩形（同一 CRS/网格对齐）。

    若外包矩形像素数 <= max_chunk_pixels，返回 [region] 单块。
    返回的子矩形均为目标 CRS 下的矩形，网格与原点对齐，可无损拼接。
    """
    if scale <= 0:
        return [region]
    try:
        bounds = region.bounds(1, crs).coordinates().getInfo()
    except Exception as exc:  # noqa: BLE001
        raise DirectDownloadError(f"无法计算 region 外包矩形: {exc}") from exc

    xs = [p[0] for p in bounds[0]]
    ys = [p[1] for p in bounds[0]]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)

    # 网格原点对齐（向下取整到 scale 的整数倍）
    x0 = math.floor(xmin / scale) * scale
    y0 = math.floor(ymin / scale) * scale
    x1 = math.ceil(xmax / scale) * scale
    y1 = math.ceil(ymax / scale) * scale

    width_px = max(1, round((x1 - x0) / scale))
    height_px = max(1, round((y1 - y0) / scale))
    total_px = width_px * height_px
    if total_px <= max_chunk_pixels:
        return [region]

    # 按宽高比确定行列数，使每块不超过 max_chunk_pixels
    ratio = width_px / height_px
    cols = max(1, math.ceil(math.sqrt(total_px / max_chunk_pixels * ratio)))
    rows = max(1, math.ceil(total_px / max_chunk_pixels / cols))
    block_w_px = math.ceil(width_px / cols)
    block_h_px = math.ceil(height_px / rows)

    chunks: list[ee.Geometry] = []
    for r in range(rows):
        for c in range(cols):
            cx0 = x0 + c * block_w_px * scale
            cx1 = min(x0 + (c + 1) * block_w_px * scale, x1)
            cy0 = y0 + r * block_h_px * scale
            cy1 = min(y0 + (r + 1) * block_h_px * scale, y1)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            chunks.append(ee.Geometry.Rectangle([cx0, cy0, cx1, cy1], crs))
    return chunks


def _merge_chunks(chunk_files: list[Path], out_path: Path,
                  bounds: list[float], scale: int, crs: str,
                  nodata: Optional[float] = None) -> Path:
    """把同一网格的分片 GeoTIFF 拼接为一张。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(chunk_files) == 1:
        # shutil.move 支持跨卷（Windows: C: 临时目录 -> D: 输出目录）
        shutil.move(str(chunk_files[0]), str(out))
        return out
    srcs = [rasterio.open(str(f)) for f in chunk_files]
    try:
        merged, transform = rio_merge(
            srcs,
            bounds=tuple(bounds),
            res=scale,
            nodata=nodata,
        )
        with rasterio.open(str(out), "w",
                           driver="GTiff",
                           height=merged.shape[1],
                           width=merged.shape[2],
                           count=merged.shape[0],
                           dtype=str(merged.dtype),
                           crs=crs,
                           transform=transform,
                           nodata=nodata) as dst:
            dst.write(merged)
        return out
    finally:
        for s in srcs:
            s.close()


def _stack_bands(band_files: list[Path], out_path: Path) -> Path:
    """把多个单波段 GeoTIFF 合成一个多波段 GeoTIFF。"""
    if len(band_files) == 1:
        shutil.move(str(band_files[0]), str(out_path))
        return Path(out_path)
    srcs = [rasterio.open(str(f)) for f in band_files]
    try:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(str(out), "w",
                           driver="GTiff",
                           height=srcs[0].height,
                           width=srcs[0].width,
                           count=len(srcs),
                           dtype=srcs[0].dtypes[0],
                           crs=srcs[0].crs,
                           transform=srcs[0].transform,
                           nodata=srcs[0].nodata) as dst:
            for i, src in enumerate(srcs, start=1):
                dst.write(src.read(1), i)
        return out
    finally:
        for s in srcs:
            s.close()


def direct_download_image(
    image: ee.Image,
    region: ee.Geometry,
    out_path: str | Path,
    scale: int,
    crs: str,
    config: Config,
    bands: Optional[list[str]] = None,
    max_chunk_pixels: int = _DEFAULT_MAX_CHUNK_PIXELS,
) -> Path:
    """把单张 ee.Image 直接下载为 GeoTIFF（必要时自动分片）。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    band_list = bands or [b["name"] for b in (image.bandNames().getInfo() or [])]
    if not band_list:
        raise DirectDownloadError("影像没有可下载的波段")

    with tempfile.TemporaryDirectory(prefix="gee_direct_") as tmpdir:
        tmp = Path(tmpdir)
        # 预计算分片（同一网格，对所有波段一致）
        chunks = plan_chunks(region, scale, crs, max_chunk_pixels)
        logger.info("直接下载分片数: %d", len(chunks))

        band_files: list[Path] = []
        for i, band in enumerate(band_list, start=1):
            chunk_files: list[Path] = []
            for ci, chunk in enumerate(chunks):
                try:
                    # 单 band 影像 + GEO_TIFF -> 返回单文件 GeoTIFF（多 band 会返回 zip）
                    url = image.select([band]).getDownloadURL({
                        "scale": scale,
                        "crs": crs,
                        "region": chunk,
                        "format": "GEO_TIFF",
                    })
                except Exception as exc:  # noqa: BLE001
                    raise DirectDownloadError(
                        f"getDownloadURL 失败（band={band}, chunk={ci}）: {exc}") from exc
                chunk_file = tmp / f"band{i:02d}_chunk{ci:02d}_{band.replace('/', '_')}.tif"
                download_url_to_file(
                    url, chunk_file,
                    timeout_s=config.network_timeout,
                    retries=config.network_retry,
                )
                chunk_files.append(chunk_file)
            band_file = tmp / f"band{i:02d}_{band.replace('/', '_')}.tif"
            if len(chunk_files) > 1:
                _merge_chunks(chunk_files, band_file, _outline_bounds(chunks, crs, scale),
                              scale, crs)
            else:
                chunk_files[0].replace(band_file)
            band_files.append(band_file)

        return _stack_bands(band_files, out)


def _outline_bounds(chunks: list[ee.Geometry], crs: str, scale: int) -> list[float]:
    """计算分片外包矩形的整体边界（供 merge 使用）。"""
    xs_all, ys_all = [], []
    for ch in chunks:
        coords = ch.coordinates().getInfo()
        for p in coords[0]:
            xs_all.append(p[0])
            ys_all.append(p[1])
    xmin = math.floor(min(xs_all) / scale) * scale
    ymin = math.floor(min(ys_all) / scale) * scale
    xmax = math.ceil(max(xs_all) / scale) * scale
    ymax = math.ceil(max(ys_all) / scale) * scale
    return [xmin, ymin, xmax, ymax]

"""Direct Download 引擎：Image.getDownloadURL()（设计文档第 18 节）。

GEE 将 getDownloadURL 定位为小块 Image 数据下载接口（请求上限约 48MiB /
50331648 字节，且按 float64 8 字节/像素计算请求大小）；本引擎支持：
1. 逐 band 请求避免多 band zip；
2. 当 region 外包矩形网格过大时自动按矩形网格分片下载，再用 rasterio 拼接；
3. 多波段用 rasterio 合成多波段 GeoTIFF；
4. 分块缓存断点续传（{out}.chunks/ 目录），失败重试只补缺失分块；
5. grid_mode="aligned" 时把 GEE 返回的分块重采样到期望对齐网格再拼接。

【重要】GEE 返回的分块网格可能与请求矩形有微小差异（如行数 1319 vs 请求的
1308、transform 原点不同）——禁止按索引直写拼接，必须按地理坐标 merge
（本引擎统一走 rasterio.merge）。分块请求一律按 float64（8B/px）核算字节，
避免 8M 像素 × 8B = 64MB 超限（P0-1 根因）。
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import ee
import httpx
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge as rio_merge
from rasterio.transform import from_origin
from rasterio.warp import reproject

from config import Config
from planner.size_estimator import (
    DEFAULT_REQUEST_BYTES_PER_PIXEL,
    aligned_grid_bounds,
    capped_request_budget,
    max_chunk_pixels_for_dtype,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# 注意：旧版本曾写死分块像素上限 8_000_000（按 float32 4B/px 假设），
# 但 GEE 按 float64 8B/px 计算请求大小：8M×8B=64MB > 48MiB 上限 → 必失败。
# 现改为按请求字节预算反推（capped_request_budget / max_chunk_pixels_for_dtype），
# 不再写死像素数（P0-1）。


class DirectDownloadError(RuntimeError):
    """直接下载失败。"""


def download_url_to_file(
    url: str,
    out_path: str | Path,
    timeout_s: float = 300.0,
    retries: int = 3,
) -> Path:
    """流式下载 URL 到本地文件，带重试（写 .part 后原子 replace，天然支持断点）。"""
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
def plan_chunk_grids(
    region: ee.Geometry,
    scale: int,
    crs: str,
    max_chunk_pixels: Optional[int] = None,
    max_request_bytes: Optional[int] = None,
    request_bytes_per_pixel: int = DEFAULT_REQUEST_BYTES_PER_PIXEL,
) -> dict:
    """把 region 的外包矩形按网格切成若干子矩形（同一 CRS/网格对齐）。

    P0-1 修复：分块像素上限按「请求字节预算 ÷ 每像素字节」反推，默认按
    float64（8B/px）核算——不再写死 8M 像素（8M×8B=64MB 会超 GEE 上限）。

    返回 {"grid": 整体对齐网格 or None, "chunks": [{geometry, x0, y0, x1, y1,
    width_px, height_px}, ...]}。所有子矩形网格与原点对齐，可无损拼接；
    且每个子矩形请求字节 <= max_request_bytes（封顶 GEE 48MiB 上限）。
    """
    budget = capped_request_budget(max_request_bytes)
    max_px = max_chunk_pixels or max_chunk_pixels_for_dtype("FLOAT64", budget)

    grid = aligned_grid_bounds(region, scale, crs)
    if grid is None:
        return {"grid": None, "chunks": [{
            "geometry": region,
            "x0": 0, "y0": 0, "x1": 0, "y1": 0,
            "width_px": 0, "height_px": 0,
        }]}

    x0, y0, x1, y1 = grid["x0"], grid["y0"], grid["x1"], grid["y1"]
    width_px, height_px = grid["width_px"], grid["height_px"]
    total_px = width_px * height_px
    if total_px <= max_px:
        return {"grid": grid, "chunks": [{
            "geometry": ee.Geometry.Rectangle([x0, y0, x1, y1], crs),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "width_px": width_px, "height_px": height_px,
        }]}

    # 按宽高比确定行列数，使每块不超过 max_px；再按字节预算收紧，
    # 保证每块请求字节 <= budget（GEE 按 float64 计费）
    ratio = width_px / height_px
    cols = max(1, math.ceil(math.sqrt(total_px / max_px * ratio)))
    rows = max(1, math.ceil(total_px / max_px / cols))
    while True:
        block_w_px = math.ceil(width_px / cols)
        block_h_px = math.ceil(height_px / rows)
        if block_w_px * block_h_px * request_bytes_per_pixel <= budget:
            break
        if rows < height_px:
            rows += 1
        elif cols < width_px:
            cols += 1
        else:
            break  # 退化为逐像元分块，必然满足字节预算

    chunks: list[dict] = []
    for r in range(rows):
        for c in range(cols):
            cx0 = x0 + c * block_w_px * scale
            cx1 = min(x0 + (c + 1) * block_w_px * scale, x1)
            cy0 = y0 + r * block_h_px * scale
            cy1 = min(y0 + (r + 1) * block_h_px * scale, y1)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            chunks.append({
                "geometry": ee.Geometry.Rectangle([cx0, cy0, cx1, cy1], crs),
                "x0": cx0, "y0": cy0, "x1": cx1, "y1": cy1,
                "width_px": max(1, round((cx1 - cx0) / scale)),
                "height_px": max(1, round((cy1 - cy0) / scale)),
            })
    return {"grid": grid, "chunks": chunks}


def plan_chunks(
    region: ee.Geometry,
    scale: int,
    crs: str,
    max_chunk_pixels: Optional[int] = None,
    max_request_bytes: Optional[int] = None,
) -> list[ee.Geometry]:
    """兼容入口：只返回分块几何（manager 用 len() 统计分片数）。

    分片上限已按 float64 字节预算反推（P0-1），默认不再写死 8M 像素。
    """
    return [c["geometry"] for c in plan_chunk_grids(
        region, scale, crs, max_chunk_pixels, max_request_bytes)["chunks"]]


def _grids_match(src, expected_transform, expected_w: int, expected_h: int, scale: int) -> bool:
    """分块栅格是否与期望的对齐网格一致（原点/旋转/尺寸容差比较）。

    必须比较 c/f（原点偏移），GEE 返回的分块 transform 原点可能与请求矩形不同。
    """
    t = src.transform
    return (
        src.width == expected_w and src.height == expected_h
        and abs(t.a - expected_transform.a) < scale * 1e-6
        and abs(t.e - expected_transform.e) < scale * 1e-6
        and abs(t.b) < 1e-9 and abs(t.d) < 1e-9
        and abs(t.c - expected_transform.c) < scale * 1e-6
        and abs(t.f - expected_transform.f) < scale * 1e-6
    )


def _resample_to_grid(
    src_path: Path, dst_path: Path,
    expected_transform, expected_w: int, expected_h: int,
) -> Path:
    """把分块重采样到期望的对齐网格（P1-4：网格对齐后再拼接）。"""
    with rasterio.open(str(src_path)) as src:
        profile = src.profile.copy()
        profile.update(width=expected_w, height=expected_h,
                       transform=expected_transform)
        dst = np.zeros((src.count, expected_h, expected_w), dtype=src.dtypes[0])
        for b in range(1, src.count + 1):
            reproject(
                source=rasterio.band(src, b),
                destination=dst[b - 1],
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=expected_transform,
                dst_crs=src.crs,
                resampling=Resampling.bilinear,
                src_nodata=src.nodata,
                dst_nodata=src.nodata,
            )
        with rasterio.open(str(dst_path), "w", **profile) as d:
            d.write(dst)
    return dst_path


def _align_chunk_file(src_path: Path, dst_path: Path, chunk_plan: dict, scale: int) -> Path:
    """把单个分块对齐到期望网格；已对齐则原样返回，否则重采样（P1-4）。"""
    expected_t = from_origin(chunk_plan["x0"], chunk_plan["y1"], scale, scale)
    with rasterio.open(str(src_path)) as s:
        if _grids_match(s, expected_t, chunk_plan["width_px"], chunk_plan["height_px"], scale):
            return src_path
        logger.info(
            "分块网格与请求矩形有差异（%dx%d vs 期望 %dx%d，transform 原点不同），"
            "重采样对齐: %s", s.width, s.height,
            chunk_plan["width_px"], chunk_plan["height_px"], src_path.name)
    return _resample_to_grid(src_path, dst_path, expected_t,
                             chunk_plan["width_px"], chunk_plan["height_px"])


def _merge_chunks(chunk_files: list[Path], out_path: Path,
                  bounds: list[float], scale: int, crs: str,
                  nodata: Optional[float] = None) -> Path:
    """把同一网格的分片 GeoTIFF 拼接为一张（按地理坐标 merge，禁止按索引直写）。

    注意：GEE 返回的分块网格可能与请求矩形有微小差异（行数/transform 原点），
    这里用 rasterio.merge 按地理坐标重投影拼接，不要改成「按数组索引直拼」。
    """
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


def stack_period_files(
    period_files: list[Path],
    out_path: str | Path,
    band_labels: Optional[list[str]] = None,
    progress_cb=None,
) -> Path:
    """把多个时间片的 GeoTIFF 堆叠为一个多波段 GeoTIFF（时间维->波段维）。

    每个输入文件（一个时间片）的全部波段按顺序写入输出：
    波段顺序 = 时间片顺序 × 每片波段顺序（如 2 天各 1 波段 -> 2 波段）。
    要求所有时间片网格一致（同 CRS / transform / 尺寸），否则抛错。
    progress_cb(frac, message)：按已写波段数回调进度（P2-7，大文件堆叠进度）。
    """
    files = [Path(f) for f in period_files]
    if len(files) == 1:
        shutil.move(str(files[0]), str(out_path))
        return Path(out_path)
    srcs = [rasterio.open(str(f)) for f in files]
    try:
        first = srcs[0]
        for s in srcs[1:]:
            if (s.width, s.height) != (first.width, first.height) \
                    or s.crs != first.crs or s.transform != first.transform:
                raise DirectDownloadError(
                    "时间片网格不一致（CRS/transform/尺寸），无法堆叠为多波段 tif；"
                    "请确认各时间片使用相同的 region/scale/crs，或改用聚合模式")
        # 统一 dtype：全部一致则保留，否则提升为 float32
        dtypes = {d for s in srcs for d in s.dtypes}
        dtype = next(iter(dtypes)) if len(dtypes) == 1 else "float32"
        total_bands = sum(s.count for s in srcs)

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(str(out), "w",
                           driver="GTiff",
                           height=first.height,
                           width=first.width,
                           count=total_bands,
                           dtype=dtype,
                           crs=first.crs,
                           transform=first.transform,
                           nodata=first.nodata) as dst:
            band_i = 1
            for src in srcs:
                for b in range(1, src.count + 1):
                    data = src.read(b)
                    if data.dtype != dtype:
                        data = data.astype(dtype)
                    dst.write(data, band_i)
                    if band_labels and band_i <= len(band_labels):
                        dst.set_band_description(band_i, str(band_labels[band_i - 1]))
                    if progress_cb:
                        progress_cb(band_i / total_bands,
                                    f"堆叠中 {band_i}/{total_bands} 波段")
                    band_i += 1
        return out
    finally:
        for s in srcs:
            s.close()


def convert_output_dtype(path: str | Path, dtype: str, compress: str = "deflate") -> Path:
    """把 GeoTIFF 转换为目标 dtype（可选 deflate 压缩），原子替换原文件。

    P2-10：GEE 返回 float64，转 float32 + deflate 可大幅减小体积
    （实测 9×180MB -> 718MB）。
    """
    p = Path(path)
    want = str(dtype).lower()
    src = rasterio.open(str(p))
    try:
        if all(d.lower() == want for d in src.dtypes):
            return p
        profile = src.profile.copy()
        profile.update(dtype=want, compress=compress)
        tmp = p.with_suffix(p.suffix + ".dtype_tmp")
        with rasterio.open(str(tmp), "w", **profile) as dst:
            for b in range(1, src.count + 1):
                data = src.read(b).astype(want)
                dst.write(data, b)
                if src.descriptions and src.descriptions[b - 1]:
                    dst.set_band_description(b, src.descriptions[b - 1])
    finally:
        src.close()
    # 源句柄已关闭后再替换（Windows 文件锁）
    tmp.replace(p)
    logger.info("输出 dtype 转换完成: %s -> %s（%s）", p, want, compress)
    return p


# ---------------------------------------------------------------- 分块缓存（P1-5）
def _cache_valid(cache_dir: Path, manifest: dict) -> bool:
    """分块缓存是否与当前请求一致（scale/crs/bands/分片数 全部匹配才复用）。"""
    mf = cache_dir / "manifest.json"
    if not mf.exists():
        return False
    try:
        return json.loads(mf.read_text(encoding="utf-8")) == manifest
    except Exception:  # noqa: BLE001
        return False


def _prepare_cache(cache_dir: Path, manifest: dict) -> None:
    """初始化分块缓存目录（参数变化则清空重建）。"""
    if not _cache_valid(cache_dir, manifest):
        shutil.rmtree(cache_dir, ignore_errors=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 主入口
def direct_download_image(
    image: ee.Image,
    region: ee.Geometry,
    out_path: str | Path,
    scale: int,
    crs: str,
    config: Config,
    bands: Optional[list[str]] = None,
    max_chunk_pixels: Optional[int] = None,
    grid_mode: str = "aligned",
    cache_chunks: bool = True,
    max_request_bytes: Optional[int] = None,
    progress_cb=None,
) -> Path:
    """把单张 ee.Image 直接下载为 GeoTIFF（必要时自动分片）。

    - 分片像素上限按 float64 字节预算反推（P0-1），保证每块请求不超 GEE 上限；
    - grid_mode="aligned"：分块对齐到期望网格后再拼接（P1-4）；
    - cache_chunks=True：分块缓存到 {out}.chunks/，失败重试只补缺失分块（P1-5）；
    - progress_cb(frac, message)：下载进度回调（P2-7）。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    band_list = bands or [b["name"] for b in (image.bandNames().getInfo() or [])]
    if not band_list:
        raise DirectDownloadError("影像没有可下载的波段")

    plan = plan_chunk_grids(region, scale, crs, max_chunk_pixels, max_request_bytes)
    chunks = plan["chunks"]
    grid = plan["grid"]
    logger.info("直接下载分片数: %d（grid=%s）", len(chunks),
                f"{grid['width_px']}x{grid['height_px']}" if grid else "N/A")

    manifest = {
        "scale": int(scale), "crs": crs,
        "bands": band_list, "chunk_count": len(chunks),
    }
    if cache_chunks:
        cache_dir = out.parent / f"{out.stem}.chunks"
        _prepare_cache(cache_dir, manifest)
        cleanup_cache = False
    else:
        cache_dir = Path(tempfile.mkdtemp(prefix="gee_direct_chunks_"))
        cleanup_cache = True

    try:
        band_files: list[Path] = []
        total = len(band_list) * len(chunks)
        done = 0
        for i, band in enumerate(band_list, start=1):
            chunk_files: list[Path] = []
            for ci, cp in enumerate(chunks):
                chunk_file = cache_dir / f"band{i:02d}_chunk{ci:02d}_{band.replace('/', '_')}.tif"
                if chunk_file.exists() and chunk_file.stat().st_size > 0:
                    logger.info("分块已缓存，跳过下载: %s", chunk_file.name)
                else:
                    try:
                        # 单 band 影像 + GEO_TIFF -> 返回单文件 GeoTIFF（多 band 会返回 zip）
                        url = image.select([band]).getDownloadURL({
                            "scale": scale,
                            "crs": crs,
                            "region": cp["geometry"],
                            "format": "GEO_TIFF",
                        })
                    except Exception as exc:  # noqa: BLE001
                        raise DirectDownloadError(
                            f"getDownloadURL 失败（band={band}, chunk={ci}）: {exc}") from exc
                    download_url_to_file(
                        url, chunk_file,
                        timeout_s=config.network_timeout,
                        retries=config.network_retry,
                    )
                done += 1
                if progress_cb:
                    progress_cb(done / total, f"下载分块 {done}/{total}（band={band}）")
                chunk_files.append(chunk_file)

            if grid_mode == "aligned" and grid is not None:
                # 网格对齐后再拼接：GEE 返回网格可能与请求矩形有微小差异
                aligned = []
                for ci, (cf, cp) in enumerate(zip(chunk_files, chunks)):
                    aligned.append(_align_chunk_file(
                        cf, cache_dir / f"aligned_band{i:02d}_chunk{ci:02d}.tif", cp, scale))
                chunk_files = aligned

            band_file = cache_dir / f"band{i:02d}_{band.replace('/', '_')}.tif"
            if len(chunk_files) > 1:
                _merge_chunks(chunk_files, band_file,
                              [grid["x0"], grid["y0"], grid["x1"], grid["y1"]],
                              scale, crs)
            else:
                chunk_files[0].replace(band_file)
            band_files.append(band_file)

        return _stack_bands(band_files, out)
    finally:
        if cleanup_cache:
            shutil.rmtree(cache_dir, ignore_errors=True)

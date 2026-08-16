"""GeoTIFF QA 引擎（设计文档第 25 节）。

文件存在 -> 打开 raster -> CRS -> resolution -> width/height -> band -> dtype -> NoData -> transform -> bounds
-> content（非零像元占比 / 值域 / 全 0 检测，P1-6）
输出 ✓ / ✗ 检查报告。

P1-6：仅查元数据无法发现「某天请求失败返回全 0」——增加内容级检查：
- 非零像元占比下限（min_valid_fraction，默认 1%，海洋/掩膜 0 属正常，全 0 视为异常）；
- min/max 值域（全 0 或常量 0 直接判失败）；
- 所有波段 dtype 一致。
大文件用 out_shape 降采样读取，避免整读 1.6GB 文件。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio

from raster.inspect import inspect_raster

CHECKS = [
    "file_exists",
    "readable",
    "crs",
    "resolution",
    "width_height",
    "bands",
    "dtype",
    "nodata",
    "transform",
    "bounds",
    "content",
]

# 内容抽查的采样像素上限（大文件按 out_shape 降采样读取）
_MAX_SAMPLE_PIXELS = 4_000_000


@dataclass
class ValidationReport:
    path: str
    passed: bool = False
    checks: list = field(default_factory=list)  # [{"check": ..., "ok": bool, "detail": str}]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "passed": self.passed,
            "checks": self.checks,
        }

    def summary(self) -> str:
        lines = []
        for c in self.checks:
            mark = "✓" if c["ok"] else "✗"
            lines.append(f"{mark} {c['detail']}")
        return "\n".join(lines)


def _content_stats(path: str | Path) -> dict:
    """内容级统计：非零像元占比 / min / max / mean（降采样读取，P1-6）。"""
    with rasterio.open(str(path)) as src:
        h, w = src.height, src.width
        if h * w > _MAX_SAMPLE_PIXELS:
            factor = math.sqrt(h * w / _MAX_SAMPLE_PIXELS)
            oh = max(1, int(h / factor))
            ow = max(1, int(w / factor))
            data = src.read(out_shape=(src.count, oh, ow))
        else:
            data = src.read()
        nodata = src.nodata

    valid = np.ones(data.shape, dtype=bool)
    if nodata is not None:
        valid &= ~np.isclose(data, nodata)
    values = data[valid]
    total = int(valid.sum())
    if total == 0:
        return {"valid_fraction": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0, "sample_pixels": int(data.size)}
    nonzero = int(np.count_nonzero(values))
    return {
        "valid_fraction": nonzero / total,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "sample_pixels": int(data.size),
    }


def validate_file(
    path: str | Path,
    expected_crs: Optional[str] = None,
    expected_scale: Optional[int] = None,
    expected_bands: Optional[int] = None,
    min_valid_fraction: float = 0.01,
    check_content: bool = True,
) -> ValidationReport:
    """对单个 GeoTIFF 执行完整 QA 检查。

    min_valid_fraction: 非零像元占比下限（低于视为异常，如全 0 输出）；
    海洋/掩膜较多的数据请适当调低（实测 ~27–35% 属正常）。
    """
    p = Path(path)
    info = inspect_raster(p)
    report = ValidationReport(path=str(p))
    checks: list[dict] = []

    def add(check: str, ok: bool, detail: str) -> None:
        checks.append({"check": check, "ok": bool(ok), "detail": detail})

    add("file_exists", info.exists, f"File exists: {p.name}")
    add("readable", info.readable,
        f"Raster readable" + (f" ({info.error})" if info.error else ""))
    if not info.readable:
        report.checks = checks
        report.passed = False
        return report

    crs_ok = (not expected_crs) or (info.crs == expected_crs)
    add("crs", crs_ok,
        f"CRS = {info.crs}" + ("" if crs_ok else f" (expected {expected_crs})"))

    if expected_scale:
        try:
            res = float(info.resolution.split("×")[0])
            res_ok = abs(res - expected_scale) <= expected_scale * 0.01
        except Exception:  # noqa: BLE001
            res_ok = False
    else:
        res_ok = bool(info.resolution)
    add("resolution", res_ok,
        f"Resolution = {info.resolution} m" + ("" if res_ok else f" (expected {expected_scale} m)"))

    wh_ok = info.width > 0 and info.height > 0
    add("width_height", wh_ok, f"Size = {info.width} × {info.height}")

    bands_ok = (not expected_bands) or (info.bands == expected_bands)
    add("bands", bands_ok, f"Bands = {info.bands}" + ("" if bands_ok else f" (expected {expected_bands})"))

    # P1-6：dtype 检查覆盖全部波段（不只第一波段）
    with rasterio.open(str(p)) as src:
        all_dtypes = set(src.dtypes)
    dtype_ok = len(all_dtypes) == 1 and bool(info.dtype)
    add("dtype", dtype_ok,
        f"dtype = {info.dtype}" + ("" if dtype_ok else f" (bands: {sorted(all_dtypes)})"))
    add("nodata", True, f"NoData = {info.nodata if info.nodata is not None else 'None'}")
    add("transform", info.transform is not None and len(info.transform) == 6,
        f"Transform = {info.transform}")
    add("bounds", info.bounds is not None and len(info.bounds) == 4,
        f"Bounds = {info.bounds}")

    # P1-6：内容级检查（非零像元占比 / 值域 / 全 0 检测）
    if check_content:
        try:
            st = _content_stats(p)
            all_zero = st["min"] == 0.0 and st["max"] == 0.0
            content_ok = st["valid_fraction"] >= min_valid_fraction and not all_zero
            add("content", content_ok,
                f"非零像元 {st['valid_fraction']:.1%} (≥{min_valid_fraction:.0%}), "
                f"min={st['min']:g}, max={st['max']:g}, mean={st['mean']:g}"
                + ("；全 0，疑似请求失败" if all_zero else ""))
        except Exception as exc:  # noqa: BLE001
            add("content", False, f"内容检查失败: {exc}")
    else:
        add("content", True, "内容检查已跳过")

    report.checks = checks
    report.passed = all(c["ok"] for c in checks)
    return report

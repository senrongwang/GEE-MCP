"""GeoTIFF QA 引擎（设计文档第 25 节）。

文件存在 -> 打开 raster -> CRS -> resolution -> width/height -> band -> dtype -> NoData -> transform -> bounds
输出 ✓ / ✗ 检查报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
]


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


def validate_file(
    path: str | Path,
    expected_crs: Optional[str] = None,
    expected_scale: Optional[int] = None,
    expected_bands: Optional[int] = None,
) -> ValidationReport:
    """对单个 GeoTIFF 执行完整 QA 检查。"""
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

    add("dtype", bool(info.dtype), f"dtype = {info.dtype}")
    add("nodata", True, f"NoData = {info.nodata if info.nodata is not None else 'None'}")
    add("transform", info.transform is not None and len(info.transform) == 6,
        f"Transform = {info.transform}")
    add("bounds", info.bounds is not None and len(info.bounds) == 4,
        f"Bounds = {info.bounds}")

    report.checks = checks
    report.passed = all(c["ok"] for c in checks)
    return report

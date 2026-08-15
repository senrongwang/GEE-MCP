"""GEE Export Task 构造与启动（设计文档第 19 节）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import ee

from utils.logging import get_logger

logger = get_logger(__name__)

# GEE Drive 文件名限制
_MAX_DRIVE_NAME = 80


class ExportError(RuntimeError):
    """Export 任务创建/启动失败。"""


@dataclass
class ExportSpec:
    description: str
    folder: str = "GEE_Exports"
    scale: int = 1000
    crs: str = "EPSG:3857"
    region: Optional[ee.Geometry] = None
    max_pixels: int = 10_000_000_000_000
    file_format: str = "GeoTIFF"
    crs_transform: Optional[list] = None
    dimensions: Optional[list] = None

    def drive_name(self) -> str:
        """生成合法的 Google Drive 文件名。"""
        name = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.description)
        name = name.strip("._")
        if len(name) > _MAX_DRIVE_NAME:
            name = name[:_MAX_DRIVE_NAME]
        return name or "gee_export"


def _apply_clip(image: ee.Image, region: ee.Geometry, clip: bool) -> ee.Image:
    """按设计文档第 13 节：默认不 clip，仅当用户明确要求时才掩膜。"""
    if clip:
        return image.clip(region)
    return image


def build_export_image(
    image: ee.Image,
    region: ee.Geometry,
    clip: bool = False,
    bands: Optional[list[str]] = None,
) -> ee.Image:
    """对导出影像做 band 选择 / 裁剪（可选）。"""
    if bands:
        image = image.select(bands)
    return _apply_clip(image, region, clip)


def start_export(
    image: ee.Image,
    spec: ExportSpec,
    clip: bool = False,
    bands: Optional[list[str]] = None,
    to_asset: Optional[str] = None,
) -> ee.batch.Task:
    """启动 Export 任务，返回 ee.batch.Task。

    默认导出到 Google Drive（设计文档第 20 节：GEE Export -> Google Drive -> 本地 Downloader）。
    可选 to_asset 导出到 GEE Asset。
    """
    image = build_export_image(image, spec.region, clip, bands)
    kwargs: dict = {
        "description": spec.description,
        "scale": spec.scale,
        "crs": spec.crs,
        "maxPixels": spec.max_pixels,
        "fileFormat": spec.file_format,
    }
    if spec.region is not None:
        kwargs["region"] = spec.region
    if spec.crs_transform is not None:
        kwargs["crsTransform"] = spec.crs_transform
    if spec.dimensions is not None:
        kwargs["dimensions"] = spec.dimensions

    try:
        if to_asset:
            task = ee.batch.Export.image.toAsset(
                image=image,
                description=spec.description,
                assetId=to_asset,
                scale=spec.scale,
                crs=spec.crs,
                region=spec.region,
                maxPixels=spec.max_pixels,
            )
        else:
            task = ee.batch.Export.image.toDrive(
                image=image,
                folder=spec.folder,
                fileNamePrefix=spec.drive_name(),
                **kwargs,
            )
        task.start()
        logger.info("Export 任务已启动: %s (id=%s)", spec.description, task.id)
        return task
    except Exception as exc:  # noqa: BLE001
        raise ExportError(f"Earth Engine export 启动失败: {exc}") from exc

"""Boundary Resolver：解析 Boundary Asset（设计文档第 13 节）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import ee

from utils.logging import get_logger

logger = get_logger(__name__)


class BoundaryError(RuntimeError):
    """Boundary Asset 解析失败。"""


@dataclass
class BoundaryInfo:
    asset_id: str
    type: str = "FeatureCollection"
    feature_count: Optional[int] = None
    bounds: list = field(default_factory=list)
    area_km2: Optional[float] = None
    crs: str = ""

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "type": self.type,
            "feature_count": self.feature_count,
            "bounds": self.bounds,
            "area_km2": self.area_km2,
            "crs": self.crs,
        }


class BoundaryResolver:
    """支持 projects/xxx/assets/Anhui 与 users/xxx/Anhui 两种格式。"""

    def __init__(self):
        pass

    def resolve(self, asset_id: str) -> tuple[ee.FeatureCollection, BoundaryInfo]:
        asset_id = asset_id.strip()
        if not asset_id:
            raise BoundaryError("boundary（Asset ID）不能为空")

        try:
            fc = ee.FeatureCollection(asset_id)
        except Exception as exc:  # noqa: BLE001
            raise BoundaryError(f"Boundary Asset 无法构造 FeatureCollection: {asset_id} —— {exc}") from exc

        info = BoundaryInfo(asset_id=asset_id)
        try:
            info.feature_count = int(fc.size().getInfo())
        except Exception as exc:  # noqa: BLE001
            raise BoundaryError(
                f"Boundary Asset 无法访问（可能不存在或无权限）: {asset_id} —— {exc}"
            ) from exc
        if info.feature_count == 0:
            raise BoundaryError(f"Boundary Asset 为空 FeatureCollection: {asset_id}")

        try:
            bounds = fc.geometry().bounds().getInfo()
            info.bounds = bounds.get("coordinates", [])
            info.crs = str(bounds.get("crs", ""))
        except Exception:  # noqa: BLE001
            logger.warning("无法获取 %s 的 bounds", asset_id)

        try:
            area = fc.geometry().area(1).getInfo()
            info.area_km2 = round(float(area) / 1e6, 3)
        except Exception:  # noqa: BLE001
            pass
        return fc, info

    def region(self, asset_id: str) -> ee.Geometry:
        """返回 boundary 的 geometry（默认 region=boundary.geometry()，见设计文档第 13 节）。"""
        fc, _ = self.resolve(asset_id)
        return fc.geometry()

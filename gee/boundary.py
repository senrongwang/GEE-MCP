"""Boundary Resolver：解析边界（设计文档第 13 节）。

支持三种边界形式（gee_download / gee_boundary_info 三选一）：
  - boundary:  Asset ID（projects/xxx/assets/xxx 或 users/xxx/xxx）
  - bbox:      [west, south, east, north]，EPSG:4326 经纬度
  - geometry:  GeoJSON Polygon / MultiPolygon / GeometryCollection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import ee

from utils.geo import (
    GeoValidationError,
    geojson_bounds,
    normalize_bbox,
    supported_geometry_type,
)
from utils.logging import get_logger

logger = get_logger(__name__)


class BoundaryError(RuntimeError):
    """边界解析失败。"""


@dataclass
class BoundaryInfo:
    asset_id: str  # 人类可读的边界描述（Asset ID / bbox / geometry 摘要）
    source_type: str = "asset"  # asset / bbox / geometry
    type: str = "FeatureCollection"
    feature_count: Optional[int] = None
    bounds: list = field(default_factory=list)
    area_km2: Optional[float] = None
    crs: str = ""

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "source_type": self.source_type,
            "type": self.type,
            "feature_count": self.feature_count,
            "bounds": self.bounds,
            "area_km2": self.area_km2,
            "crs": self.crs,
        }


class BoundaryResolver:
    """支持 Asset / bbox / GeoJSON 三种边界形式。

    - resolve()     仅解析 Asset（向后兼容）
    - resolve_any() 三选一统一入口，返回 (region: ee.Geometry, info: BoundaryInfo)
    """

    def __init__(self):
        pass

    # ================= Asset 形式 =================
    def resolve(self, asset_id: str) -> tuple[ee.FeatureCollection, BoundaryInfo]:
        asset_id = asset_id.strip()
        if not asset_id:
            raise BoundaryError("boundary（Asset ID）不能为空")

        try:
            fc = ee.FeatureCollection(asset_id)
        except Exception as exc:  # noqa: BLE001
            raise BoundaryError(f"Boundary Asset 无法构造 FeatureCollection: {asset_id} —— {exc}") from exc

        info = BoundaryInfo(asset_id=asset_id, source_type="asset")
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

        info.area_km2 = self._try_area_km2(fc.geometry())
        return fc, info

    def region(self, asset_id: str) -> ee.Geometry:
        """返回 boundary Asset 的 geometry（默认 region=boundary.geometry()，见设计文档第 13 节）。"""
        fc, _ = self.resolve(asset_id)
        return fc.geometry()

    # ================= 三选一统一入口 =================
    def resolve_any(
        self,
        boundary: Optional[str] = None,
        bbox: Optional[Any] = None,
        geometry: Optional[dict] = None,
    ) -> tuple[ee.Geometry, BoundaryInfo]:
        """解析三种边界形式之一，返回 (region, info)。

        region 恒为 ee.Geometry（Asset 取 fc.geometry()；bbox / geometry 直接构造），
        供 filterBounds / 分片规划 / 尺寸估算统一使用。
        """
        def _has(v) -> bool:
            """None 与空字符串视为未提供。"""
            return v is not None and (not isinstance(v, str) or v.strip() != "")

        provided = [
            name for name, val in
            (("boundary", boundary), ("bbox", bbox), ("geometry", geometry))
            if _has(val)
        ]
        if not provided:
            raise BoundaryError(
                "边界必须提供一种：boundary（Asset ID）/ bbox（[west,south,east,north] 经纬度）/ "
                "geometry（GeoJSON Polygon/MultiPolygon）"
            )
        if len(provided) > 1:
            raise BoundaryError(
                f"boundary / bbox / geometry 只能提供一种边界（当前同时提供了 {len(provided)} 种: {provided}）"
            )
        if boundary is not None:
            fc, info = self.resolve(str(boundary))
            return fc.geometry(), info
        if bbox is not None:
            return self._resolve_bbox(bbox)
        return self._resolve_geometry(geometry)

    def _resolve_bbox(self, bbox) -> tuple[ee.Geometry, BoundaryInfo]:
        try:
            west, south, east, north = normalize_bbox(bbox)
        except GeoValidationError as exc:
            raise BoundaryError(f"bbox 参数非法：{exc}") from exc
        geom = ee.Geometry.Rectangle([west, south, east, north])
        info = BoundaryInfo(
            asset_id=f"bbox:{west},{south},{east},{north}",
            source_type="bbox",
            type="Rectangle",
            bounds=[[[west, south], [east, south], [east, north], [west, north], [west, south]]],
            crs="EPSG:4326",
        )
        info.area_km2 = self._try_area_km2(geom)
        return geom, info

    def _resolve_geometry(self, geometry) -> tuple[ee.Geometry, BoundaryInfo]:
        gtype = supported_geometry_type(geometry)
        if gtype is None:
            raise BoundaryError(
                "geometry 必须是 GeoJSON 对象且类型为 Polygon / MultiPolygon / "
                f"GeometryCollection（Point/LineString 不支持作下载边界），收到: {geometry!r}"
            )
        try:
            geom = ee.Geometry(geometry)
        except Exception as exc:  # noqa: BLE001
            raise BoundaryError(f"geometry 无法构造 ee.Geometry: {exc}") from exc
        info = BoundaryInfo(
            asset_id=f"geometry:{gtype}",
            source_type="geometry",
            type=gtype,
            bounds=geojson_bounds(geometry),
            crs="EPSG:4326",
        )
        info.area_km2 = self._try_area_km2(geom)
        return geom, info

    # ================= 内部工具 =================
    @staticmethod
    def _try_area_km2(geom: Any) -> Optional[float]:
        """尝试计算面积（km²）；失败（未登录 / 服务器异常）返回 None。"""
        try:
            area = geom.area(1).getInfo()
            return round(float(area) / 1e6, 3)
        except Exception:  # noqa: BLE001
            return None

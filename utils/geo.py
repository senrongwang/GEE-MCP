"""纯 Python 地理工具：bbox / GeoJSON 校验与外包矩形（不依赖 GEE / 网络）。

被 models/request.py（请求校验）与 gee/boundary.py（边界解析）共用，
保证两种边界形式（bbox、GeoJSON）在任何一层都得到一致的校验结果。
"""

from __future__ import annotations

from typing import Any, Optional

# GeoJSON 中受支持的边界类型
SUPPORTED_GEOMETRY_TYPES = ("Polygon", "MultiPolygon", "GeometryCollection")


class GeoValidationError(ValueError):
    """bbox / GeoJSON 校验失败。"""


def normalize_bbox(bbox) -> list[float]:
    """校验并规范化 bbox=[west, south, east, north]（EPSG:4326 经纬度）。

    Returns:
        4 个 float 的列表 [west, south, east, north]。
    Raises:
        GeoValidationError: 长度 / 数值 / 区间 / 经纬度范围非法。
    """
    if isinstance(bbox, str) or not isinstance(bbox, (list, tuple)):
        raise GeoValidationError(
            f"bbox 必须是 [west, south, east, north] 4 个数字（EPSG:4326 经纬度），收到: {bbox!r}"
        )
    if len(bbox) != 4:
        raise GeoValidationError(
            f"bbox 需要 4 个数字 [west, south, east, north]，收到 {len(bbox)} 个: {list(bbox)!r}"
        )
    try:
        vals = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise GeoValidationError(f"bbox 元素必须是数字，收到: {list(bbox)!r}") from None
    west, south, east, north = vals
    if west >= east or south >= north:
        raise GeoValidationError(
            f"bbox 区间非法：需 west < east 且 south < north，收到 [{west}, {south}, {east}, {north}]"
        )
    if not (-180 <= west <= 180 and -180 <= east <= 180
            and -90 <= south <= 90 and -90 <= north <= 90):
        raise GeoValidationError(
            f"bbox 超出经纬度范围（经度 ±180、纬度 ±90）: {vals}"
        )
    return vals


def supported_geometry_type(geometry: Any) -> Optional[str]:
    """校验 GeoJSON 是否受支持，返回类型名（Polygon/MultiPolygon/GeometryCollection）。

    非法输入返回 None（由调用方给出友好错误），Point/LineString 等面状以外的类型不支持。
    """
    if not isinstance(geometry, dict):
        return None
    gtype = geometry.get("type")
    if gtype in ("Point", "LineString", "MultiPoint", "MultiLineString"):
        return None
    if gtype not in SUPPORTED_GEOMETRY_TYPES:
        return None
    if not isinstance(geometry.get("coordinates"), (list, tuple)) and gtype != "GeometryCollection":
        return None
    return gtype


def geojson_bounds(geometry: dict) -> list:
    """本地计算 GeoJSON 的外包矩形，与 GEE bounds().getInfo()['coordinates'] 同构。

    Returns:
        [[[west, south], [east, south], [east, north], [west, north], [west, south]]]
        空几何返回 []。
    """
    coords: list[tuple[float, float]] = []
    _collect_geojson_points(geometry, coords)
    if not coords:
        return []
    lons = [p[0] for p in coords]
    lats = [p[1] for p in coords]
    w, s, e, n = min(lons), min(lats), max(lons), max(lats)
    return [[[w, s], [e, s], [e, n], [w, n], [w, s]]]


def _collect_geojson_points(geom: Any, out: list) -> None:
    """递归收集 Polygon/MultiPolygon/GeometryCollection 内的所有 (lon, lat) 点。"""
    if not isinstance(geom, dict):
        return
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "Polygon" and isinstance(coords, list):
        for ring in coords:
            for pt in ring or []:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    out.append((float(pt[0]), float(pt[1])))
    elif gtype == "MultiPolygon" and isinstance(coords, list):
        for poly in coords:
            for ring in poly or []:
                for pt in ring or []:
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        out.append((float(pt[0]), float(pt[1])))
    elif gtype == "GeometryCollection":
        for g in geom.get("geometries") or []:
            _collect_geojson_points(g, out)

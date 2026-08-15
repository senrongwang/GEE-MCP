"""Metadata Normalizer：把 GEE STAC Catalog JSON 归一化为 DatasetRecord。

对应设计文档《GEE Dataset Discovery》第 5、7 节。
数据源：https://earthengine-stac.storage.googleapis.com/catalog/
"""

from __future__ import annotations

import re
from typing import Optional

from models.dataset import BandInfo, DatasetRecord
from utils.logging import get_logger

logger = get_logger(__name__)

# GEE STAC 类型 -> 内部类型
_GEE_TYPE_MAP = {
    "image_collection": "ImageCollection",
    "image": "Image",
    "table": "FeatureCollection",
    "feature_collection": "FeatureCollection",
}

# 标题/描述中的合成周期 -> 天数
_CADENCE_PATTERNS = [
    (re.compile(r"\b16[ -]?day\b", re.I), 16),
    (re.compile(r"\b8[ -]?day\b", re.I), 8),
    (re.compile(r"\b4[ -]?day\b", re.I), 4),
    (re.compile(r"\b3[ -]?day\b", re.I), 3),
    (re.compile(r"\b2[ -]?day\b", re.I), 2),
    (re.compile(r"\bdaily\b", re.I), 1),
    (re.compile(r"\bmonthly\b", re.I), 30),
    (re.compile(r"\bannual\b", re.I), 365),
]

# cadence 天数 -> 时间分辨率桶（设计文档第 10 节 Temporal Score 阶梯）
_CADENCE_BUCKETS = [
    (1.5, "daily"),
    (8, "8-day"),
    (16, "16-day"),
    (45, "monthly"),
    (365, "annual"),
]


def cadence_to_temporal_resolution(days: Optional[int]) -> Optional[str]:
    """把合成周期天数映射为时间分辨率桶（daily/8-day/16-day/monthly/annual）。"""
    if days is None:
        return None
    for upper, bucket in _CADENCE_BUCKETS:
        if days <= upper:
            return bucket
    return "annual"


def _first(items) -> Optional[str]:
    if not items:
        return None
    v = items[0]
    return str(v) if v is not None else None


def _parse_date(s: Optional[str]) -> Optional[str]:
    """ISO 时间 -> YYYY-MM-DD。"""
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s))
    return m.group(1) if m else None


def _parse_gsd(gsd) -> Optional[float]:
    """summaries.gsd 可能是 [250] / {"minimum":..,"maximum":..} / 250。"""
    if gsd is None:
        return None
    if isinstance(gsd, (int, float)):
        return float(gsd)
    if isinstance(gsd, list):
        vals = [float(v) for v in gsd if v is not None]
        return min(vals) if vals else None
    if isinstance(gsd, dict):
        for k in ("minimum", "min", "value"):
            if gsd.get(k) is not None:
                return float(gsd[k])
        for k in ("maximum", "max"):
            if gsd.get(k) is not None:
                return float(gsd[k])
    return None


def _provider_name(providers) -> str:
    """取 producer / licensor 名称，找不到则用 host。"""
    if not providers:
        return ""
    for p in providers:
        roles = p.get("roles") or []
        if any(r in ("producer", "licensor") for r in roles):
            name = (p.get("name") or "").strip()
            if name:
                return name
    for p in providers:
        if "host" in (p.get("roles") or []):
            name = (p.get("name") or "").strip()
            if name:
                return name
    return ""


def _cadence_days(stac: dict, title: str, description: str) -> Optional[int]:
    """gee:interval -> 天数；否则从标题/描述解析。"""
    interval = stac.get("gee:interval") or {}
    if isinstance(interval, dict):
        unit = str(interval.get("unit") or "").lower()
        val = interval.get("interval")
        if val is not None and unit in ("day", "days", "d"):
            return int(val)
        # 部分数据集 cadence 直接给天数数值
        if val is not None and unit == "cadence":
            return int(val)
    text = f"{title} {description}"
    for pat, days in _CADENCE_PATTERNS:
        if pat.search(text):
            return days
    return None


def _coverage_from_bbox(bbox) -> str:
    """根据 bbox 判断 global / 区域名（启发式）。"""
    if not bbox or len(bbox) != 4:
        return ""
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    # 覆盖全球大部分经度且纬度接近 -90..90
    if (maxx - minx) >= 300 and (maxy - miny) >= 130:
        return "global"
    # 粗略判断常见区域（供 MVP 使用，后续用 Boundary Asset 精确验证）
    region_hits = []
    for name, (rx0, ry0, rx1, ry1) in _REGION_BBOX.items():
        if _bbox_intersects((minx, miny, maxx, maxy), (rx0, ry0, rx1, ry1)):
            region_hits.append(name)
    return " ".join(region_hits) if region_hits else "regional"


# 区域近似边界（设计文档第 11 节 MVP 支持的区域）
_REGION_BBOX = {
    "China": (73.0, 18.0, 135.0, 54.0),
    "Asia": (26.0, -11.0, 180.0, 77.0),
    "Europe": (-25.0, 34.0, 45.0, 72.0),
    "North America": (-170.0, 7.0, -52.0, 84.0),
}


def _bbox_intersects(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def gee_snippet_for(dataset_id: str, type_: str) -> str:
    if type_ == "Image":
        return f"ee.Image('{dataset_id}')"
    if type_ == "FeatureCollection":
        return f"ee.FeatureCollection('{dataset_id}')"
    return f"ee.ImageCollection('{dataset_id}')"


def catalog_url_for(dataset_id: str) -> str:
    slug = dataset_id.replace("/", "_")
    return f"https://developers.google.com/earth-engine/datasets/catalog/{slug}"


def normalize_stac(stac: dict, updated_at: Optional[str] = None) -> DatasetRecord:
    """把一条 GEE STAC Collection JSON 归一化为 DatasetRecord。"""
    dataset_id = str(stac.get("id") or "").strip()
    if not dataset_id:
        raise ValueError("STAC JSON 缺少 id 字段")

    title = str(stac.get("title") or dataset_id)
    description = str(stac.get("description") or "")

    gee_type = str(stac.get("gee:type") or "").lower()
    type_ = _GEE_TYPE_MAP.get(gee_type, "ImageCollection")
    if stac.get("type") == "FeatureCollection" and type_ == "ImageCollection":
        type_ = "FeatureCollection"

    # 时间范围
    start_date = end_date = None
    extent = stac.get("extent") or {}
    temporal = (extent.get("temporal") or {}).get("interval") or []
    if temporal and temporal[0]:
        start_date = _parse_date(temporal[0][0])
        end_date = _parse_date(temporal[0][1] if len(temporal[0]) > 1 else None)

    # 空间范围
    bbox = None
    spatial = (extent.get("spatial") or {}).get("bbox") or []
    if spatial and spatial[0]:
        try:
            bbox = [float(v) for v in spatial[0][:4]]
        except (TypeError, ValueError):
            bbox = None

    # summaries
    summaries = stac.get("summaries") or {}
    platform = _first(summaries.get("platform")) or ""
    sensor = _first(summaries.get("instruments")) or ""
    gsd = _parse_gsd(summaries.get("gsd"))

    # 波段
    bands: list[BandInfo] = []
    eo_bands = summaries.get("eo:bands") or []
    for b in eo_bands:
        if not isinstance(b, dict):
            continue
        bname = str(b.get("name") or "").strip()
        if not bname:
            continue
        band = BandInfo(
            name=bname,
            description=str(b.get("description") or ""),
            scale=_as_float(b.get("gee:scale")),
        )
        bands.append(band)
    if not bands and gsd is None:
        # 某些数据集无 eo:bands：尝试 band 汇总（key 大写首字母等）
        for key, val in summaries.items():
            if key.upper() == key and isinstance(val, dict) and "minimum" in val:
                bands.append(BandInfo(name=key))

    # 空间分辨率：优先 summaries.gsd，其次波段 gsd 最小值
    if gsd is None:
        band_gsds = [_parse_gsd(b.get("gsd")) for b in eo_bands]
        band_gsds = [g for g in band_gsds if g is not None]
        if band_gsds:
            gsd = min(band_gsds)

    # 合成周期
    cadence_days = _cadence_days(stac, title, description)

    # 标签：keywords + categories + 波段名
    keywords = [str(k).strip().lower() for k in (stac.get("keywords") or []) if str(k).strip()]
    categories = [str(c).strip().lower() for c in (stac.get("gee:categories") or [])]
    tags = list(dict.fromkeys(keywords + categories + [b.name.lower() for b in bands]))

    record = DatasetRecord(
        id=dataset_id,
        name=title,
        type=type_,
        description=description,
        provider=_provider_name(stac.get("providers")),
        platform=platform,
        sensor=sensor,
        start_date=start_date,
        end_date=end_date,
        cadence_days=cadence_days,
        temporal_resolution=cadence_to_temporal_resolution(cadence_days),
        spatial_resolution=gsd,
        spatial_resolution_unit="meter",
        coverage=_coverage_from_bbox(bbox),
        bbox=bbox,
        catalog_url=catalog_url_for(dataset_id),
        gee_snippet=gee_snippet_for(dataset_id, type_),
        updated_at=updated_at,
        bands=bands,
        tags=tags,
        keywords=keywords,
    )
    return record


def normalize_seed(seed: dict, updated_at: Optional[str] = None) -> DatasetRecord:
    """把简化的 seed dict（测试 / 离线演示用）归一化为 DatasetRecord。

    seed 字段与 DatasetRecord 基本一致；bands 为 ["NDVI","EVI"] 字符串列表，
    或 [{"name": "EVI", "description": "..."}]。
    """
    data = dict(seed)
    band_specs = data.pop("bands", []) or []
    bands: list[BandInfo] = []
    for spec in band_specs:
        if isinstance(spec, str):
            bands.append(BandInfo(name=spec))
        elif isinstance(spec, dict):
            bands.append(BandInfo(
                name=str(spec.get("name") or ""),
                description=str(spec.get("description") or ""),
                scale=_as_float(spec.get("scale")),
                units=str(spec.get("units") or ""),
            ))
    bands = [b for b in bands if b.name]

    dataset_id = str(data.get("id") or "")
    type_ = str(data.get("type") or "ImageCollection")
    record = DatasetRecord(
        id=dataset_id,
        name=str(data.get("name") or dataset_id),
        type=type_,
        description=str(data.get("description") or ""),
        provider=str(data.get("provider") or ""),
        platform=str(data.get("platform") or ""),
        sensor=str(data.get("sensor") or ""),
        mission=str(data.get("mission") or ""),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
        cadence_days=_as_int(data.get("cadence_days")),
        temporal_resolution=data.get("temporal_resolution"),
        spatial_resolution=_as_float(data.get("spatial_resolution")),
        spatial_resolution_unit=str(data.get("spatial_resolution_unit") or "meter"),
        native_crs=str(data.get("native_crs") or ""),
        coverage=str(data.get("coverage") or ""),
        bbox=data.get("bbox"),
        catalog_url=str(data.get("catalog_url") or catalog_url_for(dataset_id)),
        gee_snippet=str(data.get("gee_snippet") or gee_snippet_for(dataset_id, type_)),
        updated_at=updated_at or data.get("updated_at"),
        bands=bands,
        tags=[str(t) for t in (data.get("tags") or [])],
        keywords=[str(k) for k in (data.get("keywords") or [])],
    )
    if record.temporal_resolution is None and record.cadence_days is not None:
        record.temporal_resolution = cadence_to_temporal_resolution(record.cadence_days)
    return record


def _as_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

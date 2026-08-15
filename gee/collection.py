"""ImageCollection 处理：时间筛选、边界筛选、排序、影像清单（设计文档第 11、12 节）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import ee

from utils.logging import get_logger

logger = get_logger(__name__)


class CollectionError(RuntimeError):
    """ImageCollection 筛选失败。"""


@dataclass
class ImageItem:
    id: str
    date: str
    band_count: int = 0
    cloud_cover: Optional[float] = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "date": self.date, "band_count": self.band_count}
        if self.cloud_cover is not None:
            d["cloud_cover"] = self.cloud_cover
        return d


def build_collection(
    dataset_id: str,
    start_date: str,
    end_date: str,
    boundary: ee.Geometry | None = None,
    max_items: int = 5000,
) -> ee.ImageCollection:
    """构造按时间（和可选边界）筛选后的 ImageCollection。

    单日请求（start == end）会扩展为 [start, start+1) 半开区间，
    避免 GEE 对空日期范围报错。
    """
    try:
        coll = ee.ImageCollection(dataset_id)
    except Exception as exc:  # noqa: BLE001
        raise CollectionError(f"无法构造 ImageCollection {dataset_id}: {exc}") from exc
    if start_date == end_date:
        from datetime import datetime, timedelta
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_effective = (start_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        coll = coll.filterDate(start_date, end_effective)
    else:
        coll = coll.filterDate(start_date, end_date)
    if boundary is not None:
        coll = coll.filterBounds(boundary)
    # 时间升序，便于后续按序导出
    coll = coll.sort("system:time_start", True)
    if max_items and max_items > 0:
        coll = coll.limit(max_items)
    return coll


def list_images(coll: ee.ImageCollection, max_items: int = 5000) -> list[ImageItem]:
    """列出集合内影像（id + 日期 + 云量），供 dry_run 与规划使用。"""
    items: list[ImageItem] = []
    try:
        size = int(coll.size().getInfo())
    except Exception as exc:  # noqa: BLE001
        raise CollectionError(f"无法获取集合大小: {exc}") from exc
    if size == 0:
        return items
    try:
        feats = coll.limit(max_items).getInfo().get("features", [])
    except Exception as exc:  # noqa: BLE001
        raise CollectionError(f"无法读取影像清单: {exc}") from exc

    for f in feats:
        props = f.get("properties", {})
        ts = props.get("system:time_start")
        date = _fmt_ts(ts)
        iid = (props.get("system:index") or f.get("id") or "")
        band_count = len(f.get("bands", []))
        cloud = props.get("CLOUD_COVER") or props.get("CLOUDY_PIXEL_PERCENTAGE")
        items.append(ImageItem(
            id=str(iid),
            date=date or "",
            band_count=band_count,
            cloud_cover=float(cloud) if cloud is not None else None,
        ))
    return items


def aggregate_per_period(
    coll: ee.ImageCollection,
    period: str,
    aggregation: str | None,
    bands: list[str] | None = None,
) -> ee.Image:
    """把一个时间片的集合聚合成单张 Image。

    aggregation: None=first / mean / median / mosaic / min / max / sum
    """
    if aggregation is None or aggregation == "first":
        image = coll.first()
        if image is None:
            raise CollectionError(f"{period} 时间段内没有影像")
        return image
    agg = aggregation.lower()
    try:
        if agg == "mean":
            return coll.select(bands or coll.first().bandNames()).mean()
        if agg == "median":
            return coll.select(bands or coll.first().bandNames()).median()
        if agg == "min":
            return coll.select(bands or coll.first().bandNames()).min()
        if agg == "max":
            return coll.select(bands or coll.first().bandNames()).max()
        if agg == "sum":
            return coll.select(bands or coll.first().bandNames()).sum()
        if agg == "mosaic":
            return coll.select(bands or coll.first().bandNames()).mosaic()
        if agg == "best":
            # 按云量升序取最优一景
            sorted_coll = coll.sort("CLOUD_COVER", True).sort("CLOUDY_PIXEL_PERCENTAGE", True)
            return sorted_coll.first()
    except Exception as exc:  # noqa: BLE001
        raise CollectionError(f"{period} 聚合 {aggregation} 失败: {exc}") from exc
    raise CollectionError(f"不支持的聚合方式: {aggregation}")


def _fmt_ts(ms) -> str:
    if not ms:
        return ""
    try:
        import datetime
        return datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return str(ms)

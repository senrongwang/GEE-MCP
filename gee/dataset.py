"""Dataset Resolver：判断数据集类型并收集元信息（设计文档第 9 节）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import ee

from utils.logging import get_logger

logger = get_logger(__name__)


class DatasetNotFoundError(RuntimeError):
    """数据集不存在或无法访问。"""


class DatasetType:
    IMAGE = "Image"
    IMAGE_COLLECTION = "ImageCollection"
    FEATURE_COLLECTION = "FeatureCollection"


@dataclass
class DatasetInfo:
    id: str
    type: str
    bands: list = field(default_factory=list)
    crs: str = ""
    scale: Optional[int] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    size: Optional[int] = None
    properties: dict = field(default_factory=dict)
    sample_image: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "type": self.type,
            "bands": self.bands,
            "crs": self.crs,
            "scale": self.scale,
            "time_start": self.time_start,
            "time_end": self.time_end,
        }
        if self.type == DatasetType.IMAGE_COLLECTION:
            d["image_count"] = self.size
        if self.properties:
            d["properties"] = self.properties
        return d


def _asset_type(asset_id: str) -> str:
    """通过 ee.data 查询 asset 类型；抛 DatasetNotFoundError 表示不存在。"""
    try:
        info = ee.data.getAsset(asset_id)
    except ee.EEException as exc:
        raise DatasetNotFoundError(f"Dataset 不存在或无法访问: {asset_id} —— {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise DatasetNotFoundError(f"Dataset 查询失败: {asset_id} —— {exc}") from exc
    t = (info.get("type") or "").upper()
    return t


def _band_info(image: ee.Image) -> list[dict]:
    try:
        band_names = image.bandNames().getInfo()
        proj = image.select(0).projection().getInfo() if band_names else {}
        band_info = []
        for b in band_names or []:
            info = {"name": b}
            band_info.append(info)
        return band_info, proj
    except Exception:  # noqa: BLE001
        return [], {}


class DatasetResolver:
    """解析用户提供的 dataset_id 为 Image / ImageCollection / FeatureCollection。"""

    def __init__(self):
        pass

    def inspect(self, dataset_id: str) -> DatasetInfo:
        dataset_id = dataset_id.strip()
        if not dataset_id:
            raise DatasetNotFoundError("dataset_id 不能为空")

        t = _asset_type(dataset_id)
        if t in ("IMAGE", "IMAGE_COLLECTION", "TABLE", "FOLDER", "PROJECT"):
            pass
        else:
            # 未知类型：尝试按 ImageCollection 兜底探测
            t = "IMAGE_COLLECTION"

        # FeatureCollection
        if t in ("TABLE",):
            return self._inspect_table(dataset_id)
        if t == "IMAGE":
            return self._inspect_image(dataset_id)
        return self._inspect_collection(dataset_id)

    def _inspect_image(self, dataset_id: str) -> DatasetInfo:
        image = ee.Image(dataset_id)
        info = DatasetInfo(id=dataset_id, type=DatasetType.IMAGE)
        try:
            props = image.propertyNames().getInfo()
            info.properties = {p: _shorten(image.get(p).getInfo()) for p in props[:50]}
            info.time_start = _iso_time(image.get("system:time_start").getInfo())
        except Exception:  # noqa: BLE001
            pass
        try:
            bands, proj = _band_info(image)
            info.bands = [b["name"] for b in bands]
            info.crs = str(proj.get("crs", "")) if proj else ""
            info.scale = int(proj.get("transform", [0, 0, 0, 0, 0, 0])[0]) if proj and proj.get("transform") else None
            info.sample_image = image.getInfo()
        except Exception:  # noqa: BLE001
            pass
        return info

    def _inspect_collection(self, dataset_id: str) -> DatasetInfo:
        coll = ee.ImageCollection(dataset_id)
        info = DatasetInfo(id=dataset_id, type=DatasetType.IMAGE_COLLECTION)
        try:
            info.size = int(coll.size().getInfo())
        except Exception as exc:  # noqa: BLE001
            raise DatasetNotFoundError(
                f"数据集不是可访问的 ImageCollection: {dataset_id} —— {exc}"
            ) from exc
        if info.size and info.size > 0:
            try:
                first = coll.first()
                bands, proj = _band_info(first)
                info.bands = [b["name"] for b in bands]
                info.crs = str(proj.get("crs", "")) if proj else ""
                info.scale = int(proj.get("transform", [0, 0, 0, 0, 0, 0])[0]) if proj and proj.get("transform") else None
                info.sample_image = first.getInfo()
            except Exception:  # noqa: BLE001
                pass
            try:
                ts = coll.aggregate_min("system:time_start").getInfo()
                te = coll.aggregate_max("system:time_start").getInfo()
                info.time_start = _iso_time(ts)
                info.time_end = _iso_time(te)
            except Exception:  # noqa: BLE001
                pass
        return info

    def _inspect_table(self, dataset_id: str) -> DatasetInfo:
        fc = ee.FeatureCollection(dataset_id)
        info = DatasetInfo(id=dataset_id, type=DatasetType.FEATURE_COLLECTION)
        try:
            info.size = int(fc.size().getInfo())
        except Exception:  # noqa: BLE001
            pass
        return info


def _iso_time(ms) -> Optional[str]:
    if ms is None:
        return None
    try:
        import datetime
        return datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        return str(ms)


def _shorten(value, limit: int = 200):
    """属性值截断，防止返回超大对象。"""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    return value

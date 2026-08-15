"""数据集目录模型：DatasetRecord（入库）/ DatasetCard（搜索结果卡片）。

对应设计文档《GEE Dataset Discovery》第 5 节 Dataset Card。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class BandInfo:
    name: str
    description: str = ""
    units: str = ""
    dtype: str = ""
    scale: Optional[float] = None
    offset: Optional[float] = None
    valid_min: Optional[float] = None
    valid_max: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class DatasetRecord:
    """Catalog 中的一条数据集记录（对应 datasets/bands/tags 表）。"""

    id: str
    name: str
    type: str = "ImageCollection"  # Image / ImageCollection / FeatureCollection
    description: str = ""

    provider: str = ""
    platform: str = ""
    sensor: str = ""
    mission: str = ""

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    cadence_days: Optional[int] = None
    temporal_resolution: Optional[str] = None  # daily / 8-day / 16-day / monthly / annual

    spatial_resolution: Optional[float] = None
    spatial_resolution_unit: str = "meter"
    native_crs: str = ""
    coverage: str = ""  # global / 区域名（启发式）
    bbox: Optional[list] = None  # [minx, miny, maxx, maxy]

    catalog_url: str = ""
    gee_snippet: str = ""
    updated_at: Optional[str] = None

    bands: list[BandInfo] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def band_names(self) -> list[str]:
        return [b.name for b in self.bands]

    def to_card(self, rank: int = 0, score: float = 0.0,
                match_reasons: Optional[list[str]] = None) -> dict:
        """转换为设计文档第 5 / 14 节的 Dataset Card。"""
        card = {
            "rank": rank,
            "score": round(score, 4),
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": (self.description[:300] + "...") if len(self.description) > 300
            else self.description,
            "provider": self.provider,
            "platform": self.platform,
            "sensor": self.sensor,
            "mission": self.mission,
            "spatial_resolution": self.spatial_resolution,
            "spatial_resolution_unit": self.spatial_resolution_unit,
            "native_crs": self.native_crs,
            "coverage": self.coverage,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "cadence_days": self.cadence_days,
            "temporal_resolution": self.temporal_resolution,
            "bands": self.band_names,
            "tags": self.tags[:20],
            "gee_snippet": self.gee_snippet,
            "catalog_url": self.catalog_url,
            "updated_at": self.updated_at,
            "match_reasons": match_reasons or [],
        }
        return card

    def to_full_card(self) -> dict:
        """包含 Band 详情等完整信息的卡片（gee_dataset_info 级别的补充）。"""
        card = self.to_card()
        card["band_details"] = [b.to_dict() for b in self.bands]
        return card

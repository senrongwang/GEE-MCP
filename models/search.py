"""搜索请求 / 搜索结果模型。

对应设计文档《GEE Dataset Discovery》第 4、14 节。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# 合法时间分辨率（用于校验 temporal_resolution 参数）
TEMPORAL_RESOLUTIONS = ("daily", "8-day", "16-day", "monthly", "annual")
# MVP 支持的区域（设计文档第 11 节）
REGIONS = ("global", "China", "Asia", "Europe", "North America")
# 合法数据集类型
DATASET_TYPES = ("Image", "ImageCollection", "FeatureCollection", "Table")


class SearchRequestError(ValueError):
    """搜索请求参数校验失败。"""


@dataclass
class SearchRequest:
    query: Optional[str] = None
    dataset_type: Optional[str] = None
    bands: Optional[list[str]] = None
    spatial_resolution: Optional[float] = None
    resolution_tolerance: float = 2.0  # log2 容差（设计文档第 10 节）
    temporal_resolution: Optional[str] = None
    temporal_hard: bool = False  # True 时时间分辨率不满足直接排除
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    platform: Optional[str] = None
    sensor: Optional[str] = None
    provider: Optional[str] = None
    region: Optional[str] = None
    limit: int = 10

    date_start: Optional[object] = field(default=None, init=False, repr=False)
    date_end: Optional[object] = field(default=None, init=False, repr=False)

    def validate(self) -> "SearchRequest":
        if self.limit is None:
            self.limit = 10
        self.limit = int(self.limit)
        if self.limit < 1 or self.limit > 100:
            raise SearchRequestError("limit 必须在 1..100 之间")

        if self.dataset_type:
            dt = str(self.dataset_type).strip()
            if dt not in DATASET_TYPES:
                raise SearchRequestError(
                    f"dataset_type 必须是 {DATASET_TYPES} 之一，收到: {dt!r}"
                )
            self.dataset_type = dt

        if self.temporal_resolution:
            tr = str(self.temporal_resolution).strip().lower()
            if tr not in TEMPORAL_RESOLUTIONS:
                raise SearchRequestError(
                    f"temporal_resolution 必须是 {TEMPORAL_RESOLUTIONS} 之一，收到: {tr!r}"
                )
            self.temporal_resolution = tr

        if self.spatial_resolution is not None:
            try:
                res = float(self.spatial_resolution)
            except (TypeError, ValueError):
                raise SearchRequestError(
                    f"spatial_resolution 必须是数字（米），收到: {self.spatial_resolution!r}"
                ) from None
            if res <= 0:
                raise SearchRequestError("spatial_resolution 必须为正数")
            self.spatial_resolution = res

        if self.resolution_tolerance is not None:
            tol = float(self.resolution_tolerance)
            if tol <= 0:
                raise SearchRequestError("resolution_tolerance 必须为正数")
            self.resolution_tolerance = tol

        # 日期范围（可选；提供了就校验格式与先后）
        from utils.dates import parse_date
        if self.start_date:
            self.date_start = parse_date(self.start_date)
        if self.end_date:
            self.date_end = parse_date(self.end_date)
        if self.date_start and self.date_end and self.date_end < self.date_start:
            raise SearchRequestError(
                f"end_date 早于 start_date: {self.date_start} > {self.date_end}"
            )
        if self.date_start:
            self.start_date = self.date_start.isoformat()
        if self.date_end:
            self.end_date = self.date_end.isoformat()

        if self.region:
            region = str(self.region).strip()
            region_l = region.lower()
            if region_l in ("china", "全球", "中国"):
                self.region = "China"
            elif region_l in ("asia", "亚洲"):
                self.region = "Asia"
            elif region_l in ("europe", "欧洲"):
                self.region = "Europe"
            elif region_l in ("north america", "北美洲", "northamerica"):
                self.region = "North America"
            elif region_l in ("global", "全球", "world", "worldwide"):
                self.region = "global"
            else:
                raise SearchRequestError(
                    f"region 暂只支持 {REGIONS}，收到: {region!r}"
                )

        if self.bands:
            cleaned = [str(b).strip() for b in self.bands if str(b).strip()]
            self.bands = cleaned or None

        return self

    def to_plain(self) -> dict:
        d = asdict(self)
        d.pop("date_start", None)
        d.pop("date_end", None)
        return d


@dataclass
class SearchResult:
    query: str = ""
    filters: dict = field(default_factory=dict)
    total: int = 0
    results: list = field(default_factory=list)
    excluded_no_overlap: int = 0
    warning: Optional[str] = None
    catalog_updated_at: Optional[str] = None
    catalog_count: int = 0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "filters": self.filters,
            "total": self.total,
            "results": self.results,
            "excluded_no_overlap": self.excluded_no_overlap,
            "warning": self.warning,
            "catalog": {
                "updated_at": self.catalog_updated_at,
                "dataset_count": self.catalog_count,
            },
        }

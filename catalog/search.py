"""Search Engine：Query Normalize -> 关键词检索 -> Band 检索 -> 过滤 -> Ranking -> Top N。

对应设计文档《GEE Dataset Discovery》第 8、9、10、16 节。
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

from catalog.database import CatalogDatabase
from catalog.ranking import (
    build_match_reasons,
    date_coverage_score,
    resolution_score,
    temporal_score,
    total_score,
)
from models.dataset import DatasetRecord
from models.search import SearchRequest, SearchResult
from utils.logging import get_logger

logger = get_logger(__name__)

# ---------------- 同义词表（设计文档第 8 节） ----------------
SYNONYMS: dict[str, list[str]] = {
    "evi": ["EVI", "Enhanced Vegetation Index", "enhanced vegetation"],
    "ndvi": ["NDVI", "Normalized Difference Vegetation Index"],
    "lst": ["LST", "Land Surface Temperature", "land surface temperature"],
    "soil moisture": ["soil moisture", "SM", "surface soil moisture",
                      "volumetric soil moisture"],
    "soilmoisture": ["soil moisture", "SM", "surface soil moisture",
                     "volumetric soil moisture"],
    "pet": ["PET", "potential evapotranspiration"],
    "et": ["ET", "evapotranspiration"],
    "precipitation": ["precipitation", "rainfall", "rain", "prcp"],
    "rainfall": ["precipitation", "rainfall", "rain"],
    "temperature": ["temperature", "air temperature", "temp"],
    "sif": ["SIF", "solar-induced fluorescence", "sun-induced fluorescence"],
    "gpp": ["GPP", "gross primary productivity", "gross primary production"],
    "npp": ["NPP", "net primary productivity", "net primary production"],
    "lai": ["LAI", "leaf area index"],
    "fpar": ["FPAR", "fraction of photosynthetically active radiation"],
    "albedo": ["albedo", "surface albedo"],
    "ndwi": ["NDWI", "Normalized Difference Water Index"],
    "ndsi": ["NDSI", "Normalized Difference Snow Index", "snow"],
    "snow": ["snow", "NDSI", "snow cover"],
    "landcover": ["land cover", "landcover", "land use", "land-use"],
    "land cover": ["land cover", "landcover", "land use", "land-use"],
    "dem": ["DEM", "elevation", "digital elevation model", "terrain"],
    "elevation": ["elevation", "DEM", "digital elevation model", "terrain"],
    "population": ["population", "human population"],
    "nightlights": ["night lights", "nighttime lights", "nightlights", "VIIRS"],
    "fire": ["fire", "burned area", "burn scar", "MODIS Fire"],
    "burned area": ["burned area", "fire", "burn scar"],
    "aerosol": ["aerosol", "AOD", "aerosol optical depth", "optical depth"],
    "cloud": ["cloud", "cloud mask", "cloud fraction"],
    "sar": ["SAR", "synthetic aperture radar", "backscatter", "radar"],
    "backscatter": ["backscatter", "SAR", "synthetic aperture radar"],
    "water": ["water", "NDWI", "surface water", "water body"],
    "surface water": ["surface water", "water", "NDWI", "water body"],
    "greenhouse gas": ["greenhouse gas", "GHG", "CO2", "methane", "CH4"],
    "methane": ["methane", "CH4", "greenhouse gas"],
    "soil": ["soil", "soil properties", "soil texture"],
}

# 语义上属于 Band 的别名组（从 query 推导时作为 Band 强信号）
_BAND_HINT_GROUPS = {
    "evi", "ndvi", "lst", "sif", "gpp", "npp", "lai", "fpar", "albedo",
    "ndwi", "ndsi", "snow", "sm", "soil moisture", "soilmoisture",
    "pet", "et", "aerosol", "dem", "elevation",
}


def _tokenize(text: str) -> list[str]:
    """把查询文本切成小写 token（保留 2+ 字符的词）。"""
    tokens = re.findall(r"[a-z0-9][a-z0-9\-_]{1,}", text.lower())
    return [t for t in tokens if len(t) >= 2]


def _iso_age_days(iso: str) -> Optional[int]:
    """ISO 时间距离现在的天数（兼容 Python 3.10 的 'Z' 后缀）。"""
    text = iso.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - dt).days


class QueryNormalizer:
    """把用户查询归一化为 keywords / bands / aliases（设计文档第 8 节）。"""

    def __init__(self, synonyms: Optional[dict] = None):
        self.synonyms = synonyms or SYNONYMS
        # 预构建：别名 -> 组名
        self._alias_to_group: dict[str, str] = {}
        for group, aliases in self.synonyms.items():
            for a in aliases:
                self._alias_to_group[a.lower()] = group

    def normalize(self, query: Optional[str]) -> dict:
        """返回 {"keywords": [...], "bands": [...], "aliases": [...]}。"""
        if not query or not str(query).strip():
            return {"keywords": [], "bands": [], "aliases": []}
        raw = str(query).strip()
        lower = raw.lower()
        tokens = _tokenize(raw)

        keywords: list[str] = []
        bands: list[str] = []
        aliases: list[str] = []

        # 命中同义词组：整串优先，其次逐 token
        groups: list[str] = []
        if lower in self._alias_to_group:
            groups.append(self._alias_to_group[lower])
        for t in tokens:
            g = self._alias_to_group.get(t)
            if g and g not in groups:
                groups.append(g)

        covered: set[str] = set()
        for g in groups:
            for a in self.synonyms[g]:
                if not any(k.lower() == a.lower() for k in keywords):
                    keywords.append(a)
                if a not in aliases:
                    aliases.append(a)
                covered.add(a.lower())
                covered.update(_tokenize(a))
            # Band 信号：只取该组首个别名（规范名），如 EVI / LST / soil
            if g in _BAND_HINT_GROUPS and self.synonyms[g]:
                short = self.synonyms[g][0].split()[0]
                if short not in bands:
                    bands.append(short)

        # 未被同义词覆盖的 token 作为关键词
        for t in tokens:
            if t not in covered and not any(k.lower() == t for k in keywords):
                keywords.append(t)
        # 整串（短语）也保留为关键词，便于 FTS 短语匹配
        if lower not in covered and not any(k.lower() == lower for k in keywords):
            keywords.append(raw)

        # 去重保序
        seen: set[str] = set()
        keywords = [k for k in keywords if not (k.lower() in seen or seen.add(k.lower()))]
        seen2: set[str] = set()
        bands = [b for b in bands if not (b.lower() in seen2 or seen2.add(b.lower()))]

        return {"keywords": keywords, "bands": bands, "aliases": aliases}


class SearchEngine:
    """Catalog 搜索入口：gee_search_datasets 的底层实现。"""

    def __init__(self, db: Optional[CatalogDatabase] = None,
                 normalizer: Optional[QueryNormalizer] = None):
        self.db = db
        self.normalizer = normalizer or QueryNormalizer()
        self._stale_days = 60

    def with_db(self, db: CatalogDatabase) -> "SearchEngine":
        self.db = db
        return self

    # ---------------- 主入口 ----------------
    def search(self, req: SearchRequest) -> SearchResult:
        req.validate()
        db = self.db
        if db is None:
            raise RuntimeError("SearchEngine 未绑定 CatalogDatabase")

        count = db.count_datasets()
        if count == 0:
            return SearchResult(
                query=req.query or "",
                filters=req.to_plain(),
                total=0,
                results=[],
                warning="Catalog 为空。请先运行 gee_catalog_update 收集官方 GEE 数据集目录。",
            )

        # 1) Query Normalize
        norm = self.normalizer.normalize(req.query)
        terms: list[str] = norm["keywords"]
        query_bands: list[str] = norm["bands"]
        # 明确传入的 bands 是硬过滤
        explicit_bands: list[str] = [b.lower() for b in (req.bands or [])]
        score_bands: list[str] = list(dict.fromkeys(query_bands + explicit_bands))

        # 2) 候选集
        candidate_ids: Optional[set[str]] = None
        if terms:
            candidate_ids = set(db.keyword_search(terms))
        if score_bands:
            band_ids = set(db.filter_candidates(bands_any=score_bands))
            candidate_ids = band_ids if candidate_ids is None else candidate_ids | band_ids

        filtered = db.filter_candidates(
            dataset_type=req.dataset_type,
            platform=req.platform,
            sensor=req.sensor,
            provider=req.provider,
            bands_any=explicit_bands or None,
        )
        filtered_set = set(filtered)
        if candidate_ids is not None:
            candidate_ids &= filtered_set
        else:
            candidate_ids = filtered_set

        # 3) 载入并打分
        records = db.get_many(candidate_ids)
        scored: list[tuple[float, DatasetRecord, list[str]]] = []
        excluded_no_overlap = 0
        for rec in records:
            # 硬过滤：明确要求全部 Band 必须存在
            if explicit_bands:
                band_l = {b.name.lower() for b in rec.bands}
                if not all(b in band_l for b in explicit_bands):
                    continue
            # 硬过滤：时间分辨率
            if req.temporal_hard and req.temporal_resolution:
                if rec.temporal_resolution != req.temporal_resolution:
                    continue
            # 日期无重叠直接排除
            dc, status = date_coverage_score(rec, req.date_start, req.date_end)
            if status == "NONE":
                excluded_no_overlap += 1
                continue

            reasons = build_match_reasons(
                rec,
                terms=terms,
                requested_bands=score_bands or None,
                preferred_resolution=req.spatial_resolution,
                preferred_temporal=req.temporal_resolution,
                req_start=req.date_start,
                req_end=req.date_end,
                platforms=[req.platform] if req.platform else None,
                region=req.region,
            )
            score = total_score(
                rec,
                terms=terms,
                requested_bands=score_bands or None,
                preferred_resolution=req.spatial_resolution,
                resolution_tolerance=req.resolution_tolerance,
                preferred_temporal=req.temporal_resolution,
                req_start=req.date_start,
                req_end=req.date_end,
                platforms=[req.platform] if req.platform else None,
                region=req.region,
            )
            scored.append((score, rec, reasons))

        scored.sort(key=lambda x: (-x[0], x[1].id))
        top = scored[: req.limit]

        cards = [
            rec.to_card(rank=i + 1, score=score, match_reasons=reasons)
            for i, (score, rec, reasons) in enumerate(top)
        ]

        warning = None
        updated = db.catalog_updated_at()
        if updated:
            age = _iso_age_days(updated)
            if age is not None and age > self._stale_days:
                warning = (
                    f"Catalog 元数据更新于 {updated}（{age} 天前），可能已过期。"
                    "可运行 gee_catalog_update 刷新。"
                )

        return SearchResult(
            query=req.query or "",
            filters=req.to_plain(),
            total=len(scored),
            results=cards,
            excluded_no_overlap=excluded_no_overlap,
            warning=warning,
            catalog_updated_at=updated,
            catalog_count=count,
        )

"""Catalog 包：GEE 数据集发现（搜索 / 排名 / 收集）。

对应设计文档《GEE Dataset Discovery》。
"""

from catalog.collector import CatalogCollector, CollectorConfig, collect_into
from catalog.database import CatalogDatabase, CatalogError, seed_database
from catalog.normalizer import (
    cadence_to_temporal_resolution,
    catalog_url_for,
    gee_snippet_for,
    normalize_seed,
    normalize_stac,
)
from catalog.ranking import WEIGHTS, build_match_reasons, total_score
from catalog.search import SYNONYMS, QueryNormalizer, SearchEngine

__all__ = [
    "CatalogCollector",
    "CollectorConfig",
    "collect_into",
    "CatalogDatabase",
    "CatalogError",
    "seed_database",
    "cadence_to_temporal_resolution",
    "catalog_url_for",
    "gee_snippet_for",
    "normalize_seed",
    "normalize_stac",
    "WEIGHTS",
    "build_match_reasons",
    "total_score",
    "SYNONYMS",
    "QueryNormalizer",
    "SearchEngine",
]

"""单元测试：SearchEngine（Query Normalize + 关键词/Band 检索 + 过滤 + 排序）。"""

import pytest

from catalog.database import CatalogDatabase, seed_database
from catalog.search import QueryNormalizer, SearchEngine, SYNONYMS
from catalog.seed_data import SEED_DATASETS
from models.search import SearchRequest, SearchRequestError


@pytest.fixture()
def db(tmp_path):
    db = CatalogDatabase(tmp_path / "catalog.db")
    seed_database(db, SEED_DATASETS, updated_at="2026-01-01T00:00:00Z")
    return db


@pytest.fixture()
def engine(db):
    return SearchEngine(db)


def _ids(result):
    return [r["id"] for r in result.results]


class TestQueryNormalizer:
    def test_evi(self):
        n = QueryNormalizer().normalize("EVI")
        assert "EVI" in n["keywords"]
        assert "Enhanced Vegetation Index" in n["keywords"]
        assert n["bands"] == ["EVI"]  # 只取规范别名作为 Band 信号

    def test_soil_moisture_phrase(self):
        n = QueryNormalizer().normalize("soil moisture")
        assert any("soil moisture" in k.lower() for k in n["keywords"])
        assert n["bands"], "soil moisture 应推导出 Band 信号"

    def test_lst(self):
        n = QueryNormalizer().normalize("LST")
        assert any("Land Surface Temperature" in k for k in n["keywords"])

    def test_plain_token(self):
        n = QueryNormalizer().normalize("modis")
        assert "modis" in [k.lower() for k in n["keywords"]]

    def test_empty(self):
        n = QueryNormalizer().normalize(None)
        assert n == {"keywords": [], "bands": [], "aliases": []}

    def test_synonyms_defined(self):
        for group in ("evi", "ndvi", "lst", "soil moisture", "pet"):
            assert group in SYNONYMS


class TestSearchBasic:
    def test_empty_catalog_warns(self, tmp_path):
        engine = SearchEngine(CatalogDatabase(tmp_path / "empty.db"))
        res = engine.search(SearchRequest(query="EVI"))
        assert res.total == 0
        assert "gee_catalog_update" in (res.warning or "")

    def test_query_evi_finds_modis(self, engine):
        res = engine.search(SearchRequest(query="EVI"))
        ids = _ids(res)
        assert "MODIS/061/MOD13Q1" in ids
        assert "MODIS/061/MOD13A2" in ids

    def test_query_sm_finds_gldas(self, engine):
        res = engine.search(SearchRequest(query="soil moisture"))
        assert "NASA/GLDAS/V021/NOAH/G025/T3H" in _ids(res)

    def test_limit(self, engine):
        res = engine.search(SearchRequest(query="modis", limit=3))
        assert len(res.results) <= 3

    def test_result_shape(self, engine):
        res = engine.search(SearchRequest(query="EVI", limit=5))
        card = res.results[0]
        for key in ("rank", "score", "id", "name", "type", "spatial_resolution",
                    "temporal_resolution", "start_date", "end_date", "bands",
                    "provider", "platform", "sensor", "match_reasons",
                    "gee_snippet", "catalog_url"):
            assert key in card, f"结果卡片缺少字段 {key}"


class TestSearchFilters:
    def test_band_hard_filter(self, engine):
        res = engine.search(SearchRequest(bands=["EVI"]))
        ids = _ids(res)
        assert "MODIS/061/MOD13Q1" in ids
        assert "UCSB-CHG/CHIRPS/DAILY" not in ids
        assert all("EVI" in card["bands"] for card in res.results)

    def test_dataset_type_filter(self, engine):
        res = engine.search(SearchRequest(query="MODIS", dataset_type="ImageCollection"))
        assert all(r["type"] == "ImageCollection" for r in res.results)

    def test_platform_filter(self, engine):
        res = engine.search(SearchRequest(query="EVI", platform="Aqua"))
        ids = _ids(res)
        assert "MODIS/061/MYD13Q1" in ids
        assert "MODIS/061/MOD13Q1" not in ids

    def test_provider_filter(self, engine):
        res = engine.search(SearchRequest(query="soil", provider="NASA GSFC"))
        assert "NASA/GLDAS/V021/NOAH/G025/T3H" in _ids(res)

    def test_temporal_hard(self, engine):
        res = engine.search(SearchRequest(
            query="EVI", temporal_resolution="daily", temporal_hard=True))
        ids = _ids(res)
        assert all(r["temporal_resolution"] == "daily" for r in res.results)

    def test_temporal_soft_ranks_daily_first(self, engine):
        res = engine.search(SearchRequest(query="EVI", temporal_resolution="daily"))
        ids = _ids(res)
        assert ids, "EVI 查询应返回结果"
        # MOD09GA（daily）应先于 16-day 的 MOD13Q1（如果两者都命中）
        if "MODIS/061/MOD09GA" in ids and "MODIS/061/MOD13Q1" in ids:
            assert ids.index("MODIS/061/MOD09GA") < ids.index("MODIS/061/MOD13Q1")

    def test_date_full_coverage_ranked(self, engine):
        res = engine.search(SearchRequest(
            query="EVI", start_date="2017-01-01", end_date="2021-12-31"))
        assert res.total > 0
        assert res.excluded_no_overlap >= 0

    def test_no_overlap_excluded(self, engine):
        res = engine.search(SearchRequest(
            query="MOD13Q1", start_date="1990-01-01", end_date="1995-12-31"))
        assert "MODIS/061/MOD13Q1" not in _ids(res)
        assert res.excluded_no_overlap >= 1

    def test_region(self, engine):
        res = engine.search(SearchRequest(query="EVI", region="China"))
        ids = _ids(res)
        # global 覆盖的数据集应保留
        assert "MODIS/061/MOD13Q1" in ids

    def test_resolution_preferred_orders(self, engine):
        res = engine.search(SearchRequest(query="MOD13", spatial_resolution=1000))
        ids = _ids(res)
        if "MODIS/061/MOD13A2" in ids and "MODIS/061/MOD13Q1" in ids:
            assert ids.index("MODIS/061/MOD13A2") < ids.index("MODIS/061/MOD13Q1")


class TestSearchErrors:
    def test_bad_limit(self, engine):
        with pytest.raises(SearchRequestError):
            engine.search(SearchRequest(query="EVI", limit=0))

    def test_bad_temporal(self, engine):
        with pytest.raises(SearchRequestError):
            engine.search(SearchRequest(query="EVI", temporal_resolution="weekly"))

    def test_bad_region(self, engine):
        with pytest.raises(SearchRequestError):
            engine.search(SearchRequest(query="EVI", region="Mars"))

    def test_bad_dates(self, engine):
        with pytest.raises(SearchRequestError):
            engine.search(SearchRequest(query="EVI",
                                        start_date="2021-12-31", end_date="2021-01-01"))

    def test_bad_dataset_type(self, engine):
        with pytest.raises(SearchRequestError):
            engine.search(SearchRequest(query="EVI", dataset_type="Raster"))


class TestSearchResultMeta:
    def test_catalog_meta_in_result(self, engine):
        res = engine.search(SearchRequest(query="EVI"))
        assert res.catalog_count == len(SEED_DATASETS)
        assert res.catalog_updated_at == "2026-01-01T00:00:00Z"
        assert res.to_dict()["catalog"]["dataset_count"] == len(SEED_DATASETS)

    def test_match_reasons_present(self, engine):
        res = engine.search(SearchRequest(query="EVI", bands=["EVI"]))
        assert res.results
        assert res.results[0]["match_reasons"]

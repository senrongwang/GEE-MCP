"""单元测试：server 工具注册 / Prompt / 搜索链路（注入临时 Catalog，不访问网络）。"""

import asyncio

import pytest

import server
from catalog.database import CatalogDatabase
from catalog.seed_data import SEED_DATASETS


@pytest.fixture()
def isolated_catalog(tmp_path):
    """注入临时 CatalogDatabase，隔离真实 data/gee_catalog.db。"""
    db = CatalogDatabase(tmp_path / "catalog.db")
    old = server._catalog_db
    server._catalog_db = db
    yield db
    server._catalog_db = old


class TestRegistration:
    def test_tools_registered(self):
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        for tool in ("gee_login", "gee_dataset_info", "gee_boundary_info",
                     "gee_search_datasets", "gee_validate_dataset",
                     "gee_catalog_update", "gee_download",
                     "gee_task_status", "gee_list_tasks", "gee_help"):
            assert tool in names

    def test_search_prompt_registered(self):
        names = {p.name for p in asyncio.run(server.mcp.list_prompts())}
        assert "gee_search" in names


class TestSearchTool:
    def test_seed_update(self, isolated_catalog):
        res = server.gee_catalog_update(seed=True)
        assert res["status"] == "ok"
        assert res["added"] == len(SEED_DATASETS)

    def test_search_evi(self, isolated_catalog):
        server.gee_catalog_update(seed=True)
        res = server.gee_search_datasets(query="EVI", bands=["EVI"], limit=5)
        assert res["status"] == "ok"
        assert res["total"] > 0
        top = res["results"][0]
        assert "EVI" in top["bands"]
        assert top["match_reasons"]

    def test_search_structured(self, isolated_catalog):
        """设计文档验收场景：2017-2021 年 EVI，日尺度，1km 左右。"""
        server.gee_catalog_update(seed=True)
        res = server.gee_search_datasets(
            query="EVI", bands=["EVI"], spatial_resolution=1000,
            temporal_resolution="daily", start_date="2017-01-01",
            end_date="2021-12-31", region="China", limit=10)
        assert res["status"] == "ok"
        assert res["results"], "应返回候选数据集"

    def test_empty_catalog_warns(self, isolated_catalog):
        res = server.gee_search_datasets(query="EVI")
        assert res["status"] == "ok"
        assert res["total"] == 0
        assert "gee_catalog_update" in (res["warning"] or "")

    def test_bad_param_returns_error(self, isolated_catalog):
        res = server.gee_search_datasets(query="EVI", region="Mars")
        assert res["status"] == "error"
        assert res["error"]["code"] == "search failed"

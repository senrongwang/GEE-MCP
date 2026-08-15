"""单元测试：DatasetValidator（缓存 / 错误分支，mock GEE，不访问网络）。"""

import pytest

from catalog.database import CatalogDatabase
from gee.validator import DatasetValidator


class _FakeResolver:
    """模拟 DatasetResolver.inspect 的结果。"""

    def __init__(self, outcome):
        self.outcome = outcome

    def inspect(self, dataset_id):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeInfo:
    def __init__(self, type_="ImageCollection", bands=None):
        self.type = type_
        self.bands = bands or ["NDVI", "EVI"]


@pytest.fixture()
def cache(tmp_path):
    return CatalogDatabase(tmp_path / "catalog.db")


class TestValidator:
    def test_valid_result(self, cache, monkeypatch):
        import gee.validator as gv
        original = gv.DatasetResolver
        gv.DatasetResolver = lambda: _FakeResolver(
            _FakeInfo("ImageCollection", ["NDVI", "EVI"]))
        monkeypatch.setattr("gee.validator.ensure_initialized", lambda config: None)
        try:
            v = DatasetValidator(cache=cache)
            result = v.validate("MODIS/061/MOD13Q1")
        finally:
            gv.DatasetResolver = original
        assert result["valid"] is True
        assert result["accessible"] is True
        assert result["type"] == "ImageCollection"
        assert result["bands"] == ["NDVI", "EVI"]
        assert result["cached"] is False

    def test_cache_used_on_second_call(self, cache, monkeypatch):
        calls = {"n": 0}
        from gee.dataset import DatasetNotFoundError

        def fake_resolver():
            calls["n"] += 1
            return _FakeResolver(_FakeInfo("ImageCollection", ["EVI"]))

        import gee.validator as gv
        original = gv.DatasetResolver
        gv.DatasetResolver = fake_resolver
        monkeypatch.setattr("gee.validator.ensure_initialized", lambda config: None)
        try:
            v = DatasetValidator(cache=cache, cache_ttl_hours=1)
            r1 = v.validate("DS/A")
            r2 = v.validate("DS/A")
        finally:
            gv.DatasetResolver = original
        assert r1["cached"] is False
        assert r2["cached"] is True
        assert calls["n"] == 1  # GEE 只被调用一次

    def test_not_found_result(self, cache, monkeypatch):
        from gee.dataset import DatasetNotFoundError
        import gee.validator as gv
        original = gv.DatasetResolver
        gv.DatasetResolver = lambda: _FakeResolver(
            DatasetNotFoundError("Dataset 不存在或无法访问"))
        monkeypatch.setattr("gee.validator.ensure_initialized", lambda config: None)
        try:
            v = DatasetValidator(cache=cache)
            result = v.validate("NOPE/1")
        finally:
            gv.DatasetResolver = original
        assert result["valid"] is False
        assert result["accessible"] is False
        assert "Dataset 不存在" in (result["error"] or "")

    def test_empty_id(self, cache):
        v = DatasetValidator(cache=cache)
        result = v.validate("")
        assert result["valid"] is False
        assert "dataset_id 不能为空" in (result["error"] or "")

    def test_no_cache_when_cache_none(self, monkeypatch):
        import gee.validator as gv
        original = gv.DatasetResolver
        gv.DatasetResolver = lambda: _FakeResolver(_FakeInfo("Image", ["B1"]))
        monkeypatch.setattr("gee.validator.ensure_initialized", lambda config: None)
        try:
            v = DatasetValidator(cache=None)
            result = v.validate("TEST/IMG")
        finally:
            gv.DatasetResolver = original
        assert result["valid"] is True
        assert result["cached"] is False

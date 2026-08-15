"""单元测试：Catalog 数据库（Schema / upsert / FTS / 缓存 / seed）。"""

import sqlite3

import pytest

from catalog.database import CatalogDatabase, seed_database
from catalog.seed_data import SEED_DATASETS


@pytest.fixture()
def db(tmp_path):
    return CatalogDatabase(tmp_path / "catalog.db")


@pytest.fixture()
def seeded(db):
    seed_database(db, SEED_DATASETS, updated_at="2026-01-01T00:00:00Z")
    return db


class TestSchema:
    def test_tables_exist(self, db):
        with db._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        names = {r["name"] for r in rows}
        assert {"datasets", "bands", "tags", "dataset_fts", "catalog_meta",
                "validation_cache"} <= names

    def test_fts5_available(self, db):
        with db._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='dataset_fts'"
            ).fetchall()
        assert rows, "FTS5 虚拟表未创建（Python sqlite3 需支持 FTS5）"

    def test_foreign_keys(self, db):
        with db._connect() as conn:  # noqa: SLF001
            row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1


class TestUpsert:
    def test_seed_count(self, seeded):
        assert seeded.count_datasets() == len(SEED_DATASETS)

    def test_roundtrip(self, seeded):
        rec = seeded.get_dataset("MODIS/061/MOD13Q1")
        assert rec is not None
        assert rec.name == "MOD13Q1.061 Terra Vegetation Indices 16-Day Global 250m"
        assert rec.type == "ImageCollection"
        assert rec.spatial_resolution == 250
        assert rec.temporal_resolution == "16-day"
        assert rec.provider and rec.platform == "Terra"
        assert "EVI" in rec.band_names
        assert "evi" in [t.lower() for t in rec.tags]
        assert rec.gee_snippet == "ee.ImageCollection('MODIS/061/MOD13Q1')"

    def test_upsert_update(self, seeded):
        from models.dataset import DatasetRecord
        updated = DatasetRecord(
            id="MODIS/061/MOD13Q1",
            name="NEW NAME",
            type="ImageCollection",
            bands=[],
            tags=[],
        )
        seeded.upsert_dataset(updated)
        rec = seeded.get_dataset("MODIS/061/MOD13Q1")
        assert rec.name == "NEW NAME"
        assert seeded.count_datasets() == len(SEED_DATASETS)  # 不新增

    def test_unknown_id_returns_none(self, seeded):
        assert seeded.get_dataset("NOPE/123") is None


class TestFts:
    def test_fts_search_finds_band(self, seeded):
        ids = seeded.fts_search(["evi"])
        assert "MODIS/061/MOD13Q1" in ids
        assert "MODIS/061/MOD13A2" in ids

    def test_fts_search_name(self, seeded):
        ids = seeded.fts_search(["precipitation"])
        assert "UCSB-CHG/CHIRPS/DAILY" in ids

    def test_like_fallback(self, seeded):
        ids = seeded.like_search(["gldas"])
        assert "NASA/GLDAS/V021/NOAH/G025/T3H" in ids

    def test_rebuild_fts(self, seeded):
        seeded.rebuild_fts()
        ids = seeded.fts_search(["soil moisture"])
        assert "NASA/GLDAS/V021/NOAH/G025/T3H" in ids


class TestFilter:
    def test_filter_by_type(self, seeded):
        ids = seeded.filter_candidates(dataset_type="ImageCollection")
        assert len(ids) == seeded.count_datasets()

    def test_filter_by_platform(self, seeded):
        ids = seeded.filter_candidates(platform="Terra")
        assert "MODIS/061/MOD13Q1" in ids
        assert "MODIS/061/MYD13Q1" not in ids

    def test_filter_by_band(self, seeded):
        ids = seeded.filter_candidates(bands_any=["EVI"])
        assert "MODIS/061/MOD13Q1" in ids
        assert "UCSB-CHG/CHIRPS/DAILY" not in ids


class TestValidationCache:
    def test_cache_roundtrip(self, seeded):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = {
            "dataset_id": "MODIS/061/MOD13Q1",
            "valid": True,
            "accessible": True,
            "type": "ImageCollection",
            "bands": ["NDVI", "EVI"],
            "error": None,
            "checked_at": now,
        }
        seeded.validation_cache_set("MODIS/061/MOD13Q1", result)
        got = seeded.validation_cache_get("MODIS/061/MOD13Q1", ttl_hours=1)
        assert got is not None
        assert got["valid"] is True
        assert got["bands"] == ["NDVI", "EVI"]
        assert got["cached"] is True

    def test_cache_expired(self, seeded):
        seeded.validation_cache_set("X", {
            "dataset_id": "X", "valid": True, "accessible": True,
            "type": "Image", "bands": [], "error": None,
            "checked_at": "2020-01-01T00:00:00Z",
        })
        assert seeded.validation_cache_get("X", ttl_hours=1) is None


class TestMeta:
    def test_meta_roundtrip(self, seeded):
        seeded.meta_set("updated_at", "2026-02-01T00:00:00Z")
        assert seeded.catalog_updated_at() == "2026-02-01T00:00:00Z"

    def test_stats(self, seeded):
        s = seeded.stats()
        assert s["datasets"] == len(SEED_DATASETS)
        assert s["bands"] > 0
        assert s["tags"] > 0


class TestSchemaFromSql:
    def test_schema_file_parses(self, db):
        """schema.sql 能被 sqlite3 完整执行（collector 重建时依赖）。"""
        from pathlib import Path
        schema = Path(__file__).parent.parent / "catalog" / "schema.sql"
        with db._connect() as conn:  # noqa: SLF001
            conn.executescript(schema.read_text(encoding="utf-8"))
        with db._connect() as conn:  # noqa: SLF001
            n = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        assert n == 0


class TestHrefMapMeta:
    """断点续抓依赖的 href -> dataset_id 映射（catalog_meta JSON 往返）。"""

    def test_json_roundtrip(self, db):
        import json
        mapping = {
            "https://x/catalog/MODIS/MODIS_061_MOD13Q1.json": "MODIS/061/MOD13Q1",
            "https://x/catalog/AAFC/AAFC_AAFC_AGRI.json": "AAFC/AAFC_AGRI",
        }
        db.meta_set("href_map", json.dumps(mapping))
        got = json.loads(db.meta_get("href_map") or "{}")
        assert got == mapping

    def test_missing_returns_empty(self, db):
        import json
        assert json.loads(db.meta_get("href_map") or "{}") == {}

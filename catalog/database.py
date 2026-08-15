"""Catalog 数据库：SQLite + FTS5 + 验证缓存。

对应设计文档《GEE Dataset Discovery》第 6、7、25 节。
所有操作即时开/关连接，支持多线程读；写入由内部锁串行化。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Optional

from catalog.normalizer import normalize_seed
from models.dataset import BandInfo, DatasetRecord
from utils.logging import get_logger

logger = get_logger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> Optional[datetime.datetime]:
    """解析 ISO 时间（兼容 Python 3.10 不识别 'Z' 后缀）。"""
    if not s:
        return None
    text = s.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _fts_escape(term: str) -> str:
    """转义 FTS5 查询词中的特殊字符（放入双引号内安全匹配）。"""
    return term.replace('"', '""')


class CatalogError(RuntimeError):
    """Catalog 数据库操作失败。"""


class CatalogDatabase:
    """GEE Dataset Catalog 的 SQLite 存储层。"""

    def __init__(self, db_path: str | Path, schema_path: Optional[Path] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_path = schema_path or _SCHEMA_PATH
        self._write_lock = threading.Lock()
        self.init_schema()

    # ---------------- 连接 ----------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---------------- Schema ----------------
    def init_schema(self) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.executescript(self._schema_path.read_text(encoding="utf-8"))

    # ---------------- 元信息 ----------------
    def meta_get(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM catalog_meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO catalog_meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    def catalog_updated_at(self) -> Optional[str]:
        return self.meta_get("updated_at")

    # ---------------- 写入 ----------------
    def upsert_dataset(self, record: DatasetRecord) -> None:
        """插入或更新一条数据集记录（datasets + bands + tags + FTS）。"""
        with self._write_lock:
            with self._connect() as conn:
                self._upsert_locked(conn, record)

    def upsert_many(self, records: Iterable[DatasetRecord]) -> int:
        """批量 upsert，返回写入条数。"""
        count = 0
        with self._write_lock:
            with self._connect() as conn:
                for record in records:
                    self._upsert_locked(conn, record)
                    count += 1
        return count

    def _upsert_locked(self, conn: sqlite3.Connection, record: DatasetRecord) -> None:
        now = record.updated_at or _utc_now()
        bbox_json = json.dumps(record.bbox) if record.bbox else None
        conn.execute(
            """
            INSERT INTO datasets(
                id, name, type, description, provider, platform, sensor, mission,
                start_date, end_date, cadence_days, temporal_resolution,
                spatial_resolution, spatial_resolution_unit, native_crs, coverage,
                bbox, catalog_url, gee_snippet, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, type=excluded.type, description=excluded.description,
                provider=excluded.provider, platform=excluded.platform,
                sensor=excluded.sensor, mission=excluded.mission,
                start_date=excluded.start_date, end_date=excluded.end_date,
                cadence_days=excluded.cadence_days,
                temporal_resolution=excluded.temporal_resolution,
                spatial_resolution=excluded.spatial_resolution,
                spatial_resolution_unit=excluded.spatial_resolution_unit,
                native_crs=excluded.native_crs, coverage=excluded.coverage,
                bbox=excluded.bbox, catalog_url=excluded.catalog_url,
                gee_snippet=excluded.gee_snippet, updated_at=excluded.updated_at
            """,
            (
                record.id, record.name, record.type, record.description,
                record.provider, record.platform, record.sensor, record.mission,
                record.start_date, record.end_date, record.cadence_days,
                record.temporal_resolution, record.spatial_resolution,
                record.spatial_resolution_unit, record.native_crs, record.coverage,
                bbox_json, record.catalog_url, record.gee_snippet, now,
            ),
        )
        # bands
        conn.execute("DELETE FROM bands WHERE dataset_id=?", (record.id,))
        conn.executemany(
            "INSERT INTO bands(dataset_id, name, description, units, dtype, scale, "
            "offset, valid_min, valid_max) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (record.id, b.name, b.description, b.units, b.dtype,
                 b.scale, b.offset, b.valid_min, b.valid_max)
                for b in record.bands
            ],
        )
        # tags
        conn.execute("DELETE FROM tags WHERE dataset_id=?", (record.id,))
        conn.executemany(
            "INSERT OR IGNORE INTO tags(dataset_id, tag) VALUES(?,?)",
            [(record.id, t) for t in record.tags],
        )
        # FTS
        conn.execute("DELETE FROM dataset_fts WHERE dataset_id=?", (record.id,))
        conn.execute(
            "INSERT INTO dataset_fts(dataset_id, name, description, tags, bands) "
            "VALUES(?,?,?,?,?)",
            (
                record.id,
                record.name,
                record.description,
                " ".join(record.tags),
                " ".join(b.name.lower() for b in record.bands),
            ),
        )

    def rebuild_fts(self) -> None:
        """全量重建 FTS5（collector 结束后调用）。"""
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM dataset_fts")
                rows = conn.execute(
                    "SELECT id, name, description, type FROM datasets"
                ).fetchall()
                band_map: dict[str, list[str]] = {}
                tag_map: dict[str, list[str]] = {}
                for r in conn.execute(
                        "SELECT dataset_id, name FROM bands ORDER BY id"):
                    band_map.setdefault(r["dataset_id"], []).append(r["name"])
                for r in conn.execute("SELECT dataset_id, tag FROM tags"):
                    tag_map.setdefault(r["dataset_id"], []).append(r["tag"])
                conn.executemany(
                    "INSERT INTO dataset_fts(dataset_id, name, description, tags, bands) "
                    "VALUES(?,?,?,?,?)",
                    [
                        (r["id"], r["name"], r["description"],
                         " ".join(tag_map.get(r["id"], [])),
                         " ".join(b.lower() for b in band_map.get(r["id"], [])))
                        for r in rows
                    ],
                )

    def clear(self) -> None:
        """清空全部数据（重新收集前调用）。"""
        with self._write_lock:
            with self._connect() as conn:
                for table in ("dataset_fts", "bands", "tags", "datasets",
                              "catalog_meta", "validation_cache"):
                    conn.execute(f"DELETE FROM {table}")

    # ---------------- 查询 ----------------
    def count_datasets(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM datasets").fetchone()
        return int(row["n"]) if row else 0

    def stats(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, "
                "(SELECT COUNT(*) FROM bands) AS bands, "
                "(SELECT COUNT(*) FROM tags) AS tags "
                "FROM datasets"
            ).fetchone()
        return {
            "datasets": int(row["n"]) if row else 0,
            "bands": int(row["bands"]) if row else 0,
            "tags": int(row["tags"]) if row else 0,
            "updated_at": self.catalog_updated_at(),
            "db_path": str(self.db_path),
        }

    def get_dataset(self, dataset_id: str) -> Optional[DatasetRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE id=?", (dataset_id,)
            ).fetchone()
            if row is None:
                return None
            band_rows = conn.execute(
                "SELECT * FROM bands WHERE dataset_id=? ORDER BY id", (dataset_id,)
            ).fetchall()
            tag_rows = conn.execute(
                "SELECT tag FROM tags WHERE dataset_id=? ORDER BY tag", (dataset_id,)
            ).fetchall()
        return self._row_to_record(row, band_rows, tag_rows)

    def get_many(self, dataset_ids: Iterable[str]) -> list[DatasetRecord]:
        ids = list(dataset_ids)
        if not ids:
            return []
        out: dict[str, DatasetRecord] = {}
        with self._connect() as conn:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT * FROM datasets WHERE id IN ({placeholders})", ids
            ).fetchall()
            band_rows = conn.execute(
                f"SELECT * FROM bands WHERE dataset_id IN ({placeholders}) ORDER BY id",
                ids,
            ).fetchall()
            tag_rows = conn.execute(
                f"SELECT tag, dataset_id FROM tags WHERE dataset_id IN ({placeholders})",
                ids,
            ).fetchall()
        for row in rows:
            out[row["id"]] = self._row_to_record(
                row,
                [b for b in band_rows if b["dataset_id"] == row["id"]],
                [t for t in tag_rows if t["dataset_id"] == row["id"]],
            )
        return [out[i] for i in ids if i in out]

    def all_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM datasets ORDER BY id").fetchall()
        return [r["id"] for r in rows]

    def _row_to_record(self, row, band_rows, tag_rows) -> DatasetRecord:
        bbox = None
        if row["bbox"]:
            try:
                bbox = json.loads(row["bbox"])
            except (TypeError, ValueError):
                bbox = None
        bands = [
            BandInfo(
                name=b["name"], description=b["description"] or "",
                units=b["units"] or "", dtype=b["dtype"] or "",
                scale=b["scale"], offset=b["offset"],
                valid_min=b["valid_min"], valid_max=b["valid_max"],
            )
            for b in band_rows
        ]
        return DatasetRecord(
            id=row["id"], name=row["name"], type=row["type"],
            description=row["description"] or "",
            provider=row["provider"] or "", platform=row["platform"] or "",
            sensor=row["sensor"] or "", mission=row["mission"] or "",
            start_date=row["start_date"], end_date=row["end_date"],
            cadence_days=row["cadence_days"],
            temporal_resolution=row["temporal_resolution"],
            spatial_resolution=row["spatial_resolution"],
            spatial_resolution_unit=row["spatial_resolution_unit"] or "meter",
            native_crs=row["native_crs"] or "", coverage=row["coverage"] or "",
            bbox=bbox, catalog_url=row["catalog_url"] or "",
            gee_snippet=row["gee_snippet"] or "", updated_at=row["updated_at"],
            bands=bands, tags=[t["tag"] for t in tag_rows],
        )

    # ---------------- 全文检索 ----------------
    def fts_search(self, terms: list[str], limit: int = 500) -> list[str]:
        """FTS5 关键词检索，返回 dataset_id 列表（按相关性降序）。

        查询词做转义后跨 name/description/tags/bands 列 OR 匹配。
        """
        if not terms:
            return []
        query = " OR ".join(
            f'"{_fts_escape(t)}"' for t in terms if t.strip()
        )
        if not query:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT dataset_id FROM dataset_fts "
                    "WHERE dataset_fts MATCH ? LIMIT ?",
                    (query, limit),
                ).fetchall()
            return [r["dataset_id"] for r in rows]
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 检索失败（%s），降级为 LIKE 检索", exc)
            return []

    def like_search(self, terms: list[str], limit: int = 500) -> list[str]:
        """LIKE 兜底检索（FTS5 不可用或未命中时）。"""
        if not terms:
            return []
        pats = [f"%{t.lower()}%" for t in terms if t.strip()]
        if not pats:
            return []
        # 任意关键词命中即可（OR）
        per_pat = ("LOWER(name) LIKE ? OR LOWER(description) LIKE ? "
                   "OR LOWER(provider) LIKE ? OR LOWER(platform) LIKE ?")
        conditions = " OR ".join(per_pat for _ in pats)
        params = [p for pat in pats for p in (pat, pat, pat, pat)]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT id FROM datasets WHERE {conditions} LIMIT ?",
                (*params, limit),
            ).fetchall()
        ids = [r["id"] for r in rows]
        # 再加 tags / bands 命中
        extra: set[str] = set()
        with self._connect() as conn:
            for pat in pats:
                for r in conn.execute(
                        "SELECT DISTINCT dataset_id FROM tags WHERE LOWER(tag) LIKE ?",
                        (pat,)):
                    extra.add(r["dataset_id"])
                for r in conn.execute(
                        "SELECT DISTINCT dataset_id FROM bands WHERE LOWER(name) LIKE ?",
                        (pat,)):
                    extra.add(r["dataset_id"])
        return list(dict.fromkeys(ids + sorted(extra)))[:limit]

    def keyword_search(self, terms: list[str], limit: int = 500) -> list[str]:
        ids = self.fts_search(terms, limit=limit)
        if not ids:
            ids = self.like_search(terms, limit=limit)
        return ids

    def filter_candidates(
        self,
        dataset_type: Optional[str] = None,
        platform: Optional[str] = None,
        sensor: Optional[str] = None,
        provider: Optional[str] = None,
        bands_any: Optional[list[str]] = None,
        limit: int = 2000,
    ) -> list[str]:
        """结构化过滤，返回候选 dataset_id 列表。"""
        sql = "SELECT DISTINCT d.id FROM datasets d"
        joins: list[str] = []
        where: list[str] = []
        params: list = []

        if bands_any:
            joins.append("JOIN bands b ON b.dataset_id = d.id")
            placeholders = ",".join("?" * len(bands_any))
            where.append(f"LOWER(b.name) IN ({placeholders})")
            params.extend(b.lower() for b in bands_any)
        if dataset_type:
            where.append("d.type = ?")
            params.append(dataset_type)
        if platform:
            where.append("LOWER(d.platform) LIKE ?")
            params.append(f"%{platform.lower()}%")
        if sensor:
            where.append("LOWER(d.sensor) LIKE ?")
            params.append(f"%{sensor.lower()}%")
        if provider:
            where.append("LOWER(d.provider) LIKE ?")
            params.append(f"%{provider.lower()}%")

        sql += " " + " ".join(joins)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY d.id LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [r["id"] for r in rows]

    # ---------------- 验证缓存（设计文档第 25 节） ----------------
    def validation_cache_get(self, dataset_id: str,
                             ttl_hours: float = 1.0) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM validation_cache WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
        if row is None:
            return None
        checked = _parse_iso(row["checked_at"])
        if checked is None:
            return None
        age = (datetime.datetime.now(datetime.timezone.utc) - checked).total_seconds()
        if age > ttl_hours * 3600:
            return None
        bands = []
        if row["bands"]:
            try:
                bands = json.loads(row["bands"])
            except (TypeError, ValueError):
                bands = []
        return {
            "dataset_id": dataset_id,
            "valid": bool(row["valid"]),
            "accessible": bool(row["accessible"]),
            "type": row["type"],
            "bands": bands,
            "error": row["error"],
            "checked_at": row["checked_at"],
            "cached": True,
        }

    def validation_cache_set(self, dataset_id: str, result: dict) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO validation_cache(
                        dataset_id, valid, accessible, type, bands, error, checked_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(dataset_id) DO UPDATE SET
                        valid=excluded.valid, accessible=excluded.accessible,
                        type=excluded.type, bands=excluded.bands,
                        error=excluded.error, checked_at=excluded.checked_at
                    """,
                    (
                        dataset_id,
                        1 if result.get("valid") else 0,
                        1 if result.get("accessible") else 0,
                        result.get("type") or "",
                        json.dumps(result.get("bands") or [], ensure_ascii=False),
                        result.get("error"),
                        result.get("checked_at") or _utc_now(),
                    ),
                )


def seed_database(db: CatalogDatabase, seed_records: list[dict],
                  updated_at: Optional[str] = None) -> int:
    """把 seed 数据写入 Catalog（测试 / 离线演示）。"""
    if updated_at is None:
        updated_at = _utc_now()
    records = [normalize_seed(s, updated_at=updated_at) for s in seed_records]
    count = db.upsert_many(records)
    db.rebuild_fts()
    db.meta_set("updated_at", updated_at)
    db.meta_set("source", "bundled-seed")
    db.meta_set("dataset_count", str(count))
    return count

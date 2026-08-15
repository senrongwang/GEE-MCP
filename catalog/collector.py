"""Catalog Collector：从官方 GEE STAC Catalog 收集数据集元数据并入库。

对应设计文档《GEE Dataset Discovery》第 7、24 节。

数据源：https://earthengine-stac.storage.googleapis.com/catalog/catalog.json
流程： 官方 Catalog -> 遍历子目录 -> 逐数据集 JSON -> Normalizer -> SQLite -> FTS5

特性：
- 并发抓取（默认 8 线程）、超时重试、断点续抓（同一天已入库的跳过）
- 支持 limit（部分收集，测试 / 快速体验用）
- 提供内置 seed 数据（离线演示 / 单元测试，不访问网络）
"""

from __future__ import annotations

import datetime
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from typing import Callable, Iterable, Optional

from catalog.database import CatalogDatabase
from catalog.normalizer import normalize_stac
from catalog.seed_data import SEED_DATASETS
from utils.logging import get_logger

logger = get_logger(__name__)

STAC_ROOT_URL = "https://storage.googleapis.com/earthengine-stac/catalog/catalog.json"
_USER_AGENT = "ai-gee-downloader/0.2 (catalog collector)"

ProgressCb = Callable[[dict], None]


class CatalogCollectError(RuntimeError):
    """Catalog 收集失败。"""


class CollectorConfig:
    def __init__(self, root_url: str = STAC_ROOT_URL, concurrency: int = 8,
                 request_timeout: float = 60.0, retry: int = 3,
                 max_depth: int = 5, user_agent: str = _USER_AGENT):
        self.root_url = root_url
        self.concurrency = concurrency
        self.request_timeout = request_timeout
        self.retry = retry
        self.max_depth = max_depth
        self.user_agent = user_agent


def _http_get_json(url: str, timeout: float, user_agent: str,
                   retry: int = 3) -> dict:
    """带重试的 GET + JSON 解析。"""
    last_err: Optional[Exception] = None
    for attempt in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError, TimeoutError) as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1))
    raise CatalogCollectError(f"请求失败: {url} —— {last_err}")


def walk_catalog(root_url: str, cfg: CollectorConfig) -> list[str]:
    """遍历 STAC 目录树，返回全部数据集 JSON href 列表。"""
    hrefs: list[str] = []
    seen_catalogs: set[str] = set()

    def walk(cat_url: str, depth: int) -> None:
        if depth > cfg.max_depth or cat_url in seen_catalogs:
            return
        seen_catalogs.add(cat_url)
        cat = _http_get_json(cat_url, cfg.request_timeout, cfg.user_agent, cfg.retry)
        for link in cat.get("links") or []:
            if link.get("rel") != "child":
                continue
            href = link.get("href") or ""
            if not href:
                continue
            href = urllib.parse.urljoin(cat_url, href)
            if href.endswith("catalog.json"):
                walk(href, depth + 1)
            elif href.endswith(".json"):
                hrefs.append(href)

    walk(root_url, 0)
    return hrefs


def _fetch_one(url: str, cfg: CollectorConfig) -> dict:
    return _http_get_json(url, cfg.request_timeout, cfg.user_agent, cfg.retry)


class CatalogCollector:
    """并发抓取 STAC 数据集并写入 SQLite。"""

    def __init__(self, db: CatalogDatabase, config: Optional[CollectorConfig] = None):
        self.db = db
        self.config = config or CollectorConfig()
        self._queue: Queue = Queue()
        self._writer: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---------------- 主入口 ----------------
    def collect(self, force: bool = False, limit: Optional[int] = None,
                progress_cb: Optional[ProgressCb] = None) -> dict:
        """收集官方 GEE Catalog 到本地 SQLite。

        Args:
            force: True 时全量重建（清空后重新收集）；False 时跳过同一天已入库的数据集
            limit: 最多收集的数据集数量（None=全部；用于快速体验 / 测试）
            progress_cb: 进度回调（接收 stats dict）
        """
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        # force=True：全量重建（先清空，避免残留旧数据）
        if force:
            self.db.clear()
            existing_today = set()
            href_map: dict[str, str] = {}
        else:
            # 断点续抓：href -> dataset_id 映射来自上次收集，当天已入库的跳过
            existing_today = self._existing_ids(today)
            try:
                href_map = json.loads(self.db.meta_get("href_map") or "{}")
            except (TypeError, ValueError):
                href_map = {}

        started = time.time()
        stats = {
            "state": "running",
            "found": 0, "fetched": 0, "added": 0, "skipped": 0,
            "failed": 0, "elapsed_s": 0.0,
        }

        def report():
            stats["elapsed_s"] = round(time.time() - started, 1)
            if progress_cb:
                progress_cb(dict(stats))

        # 1) 遍历目录树
        try:
            hrefs = walk_catalog(self.config.root_url, self.config)
        except CatalogCollectError as exc:
            stats.update({"state": "failed", "error": str(exc)})
            report()
            return stats
        stats["found"] = len(hrefs)
        report()

        # 2) 并发抓取 + 单写线程入库
        self._queue = Queue()
        writer = threading.Thread(
            target=self._writer_loop, args=(today, stats, href_map), daemon=True,
            name="catalog-writer",
        )
        writer.start()

        submitted = 0
        try:
            with ThreadPoolExecutor(max_workers=self.config.concurrency) as pool:
                futures = {}
                for href in hrefs:
                    if limit is not None and submitted >= limit:
                        break
                    # 断点续抓：上次已知该 href 对应当天已入库的数据集 -> 跳过
                    if existing_today and href_map.get(href) in existing_today:
                        stats["skipped"] += 1
                        continue
                    futures[pool.submit(_fetch_one, href, self.config)] = href
                    submitted += 1
                    if len(futures) >= self.config.concurrency * 4:
                        self._drain(futures, stats, report)
                self._drain(futures, stats, report)
        finally:
            self._queue.put(None)
            writer.join(timeout=120)

        stats["elapsed_s"] = round(time.time() - started, 1)

        # 3) 重建 FTS、记录更新时间与 href 映射（供下次断点续抓）
        self.db.rebuild_fts()
        self.db.meta_set("updated_at",
                         datetime.datetime.now(datetime.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.db.meta_set("source", self.config.root_url)
        self.db.meta_set("dataset_count", str(self.db.count_datasets()))
        if href_map:
            self.db.meta_set("href_map", json.dumps(href_map))

        stats["state"] = "completed"
        stats.update(self.db.stats())
        report()
        return stats

    def _existing_ids(self, today: str) -> set[str]:
        """返回 updated_at 为 today 的 dataset_id（断点续抓用）。"""
        import sqlite3
        try:
            with self.db._connect() as conn:  # noqa: SLF001
                rows = conn.execute(
                    "SELECT id FROM datasets WHERE updated_at LIKE ?",
                    (f"{today}%",),
                ).fetchall()
            return {r["id"] for r in rows}
        except sqlite3.Error:
            return set()

    def _drain(self, futures: dict, stats: dict, report) -> None:
        done = 0
        for fut in as_completed(futures):
            href = futures[fut]
            try:
                stac = fut.result()
                stats["fetched"] += 1
                self._queue.put((href, stac))
            except CatalogCollectError as exc:
                stats["failed"] += 1
                logger.warning("抓取失败: %s —— %s", href, exc)
            done += 1
            if done % 25 == 0:
                report()
        futures.clear()

    def _writer_loop(self, today: str, stats: dict, href_map: dict) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            try:
                href, stac = item
                record = normalize_stac(stac, updated_at=today)
                href_map[href] = record.id
                self.db.upsert_dataset(record)
                stats["added"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                logger.warning("归一化失败: %s", exc)
            finally:
                self._queue.task_done()

    # ---------------- Seed（离线 / 测试） ----------------
    def collect_seed(self, progress_cb: Optional[ProgressCb] = None) -> dict:
        """把内置 seed 数据写入 Catalog（不访问网络）。"""
        started = time.time()
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        records = [normalize_stac_seed(s, today) for s in SEED_DATASETS]
        count = self.db.upsert_many(records)
        self.db.rebuild_fts()
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.meta_set("updated_at", now)
        self.db.meta_set("source", "bundled-seed")
        stats = {
            "state": "completed",
            "added": count,
            "skipped": 0,
            "failed": 0,
            "elapsed_s": round(time.time() - started, 1),
            "source": "bundled-seed",
            **self.db.stats(),
        }
        if progress_cb:
            progress_cb(stats)
        return stats


def normalize_stac_seed(seed: dict, updated_at: Optional[str] = None):
    """collect_seed 用：把 seed dict 归一化为 DatasetRecord。"""
    from catalog.normalizer import normalize_seed
    data = dict(seed)
    if "updated_at" not in data:
        data["updated_at"] = updated_at
    return normalize_seed(data)


def collect_into(db: CatalogDatabase, cfg: Optional[CollectorConfig] = None,
                 force: bool = False, limit: Optional[int] = None,
                 progress_cb: Optional[ProgressCb] = None) -> dict:
    """便捷入口：构造 Collector 并收集。"""
    return CatalogCollector(db, cfg).collect(force=force, limit=limit,
                                             progress_cb=progress_cb)

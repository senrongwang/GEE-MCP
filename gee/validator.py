"""Dataset Validator：用当前 GEE 账号验证 Catalog 候选数据集。

对应设计文档《GEE Dataset Discovery》第 12、13、25、28 节。

- 搜索只回答“官方目录里有哪些”，验证回答“当前账号能否访问 / 类型 / Band 是否真实存在”。
- 验证结果缓存（validation_cache，默认 TTL 1 小时），避免重复调用 GEE API。
- 验证需要 GEE 登录（与搜索不同，搜索不需要）。
"""

from __future__ import annotations

import datetime
from typing import Optional

from config import Config
from gee.auth import ensure_initialized
from gee.dataset import DatasetNotFoundError, DatasetResolver
from utils.logging import get_logger

logger = get_logger(__name__)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DatasetValidator:
    """验证数据集是否可访问、类型与真实 Band 列表（带缓存）。"""

    def __init__(self, config: Optional[Config] = None,
                 cache=None, cache_ttl_hours: float = 1.0):
        self.config = config or Config.load()
        # cache：可选注入（CatalogDatabase 实例）；为 None 时不做缓存
        self.cache = cache
        self.cache_ttl_hours = cache_ttl_hours or float(
            self.config.data.get("catalog", {}).get("validation_cache_ttl_hours", 1.0))

    def validate(self, dataset_id: str, use_cache: bool = True) -> dict:
        """验证数据集；结果结构见设计文档第 13 节。

        Returns:
            {
              "valid": bool, "accessible": bool, "type": str,
              "bands": [...], "error": str|None, "checked_at": str,
              "cached": bool
            }
        """
        dataset_id = str(dataset_id or "").strip()
        if not dataset_id:
            return {
                "valid": False, "accessible": False, "type": "",
                "bands": [], "error": "dataset_id 不能为空",
                "checked_at": _utc_now(), "cached": False,
            }

        if use_cache and self.cache is not None:
            cached = self.cache.validation_cache_get(dataset_id, self.cache_ttl_hours)
            if cached:
                return cached

        ensure_initialized(self.config)
        try:
            info = DatasetResolver().inspect(dataset_id)
            result = {
                "valid": True,
                "accessible": True,
                "type": info.type,
                "bands": info.bands,
                "error": None,
                "checked_at": _utc_now(),
                "cached": False,
            }
        except DatasetNotFoundError as exc:
            result = {
                "valid": False,
                "accessible": False,
                "type": "",
                "bands": [],
                "error": str(exc),
                "checked_at": _utc_now(),
                "cached": False,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("验证 %s 失败: %s", dataset_id, exc)
            result = {
                "valid": False,
                "accessible": False,
                "type": "",
                "bands": [],
                "error": str(exc),
                "checked_at": _utc_now(),
                "cached": False,
            }

        if self.cache is not None:
            try:
                self.cache.validation_cache_set(dataset_id, result)
            except Exception:  # noqa: BLE001
                logger.warning("写入验证缓存失败: %s", dataset_id)
        return result

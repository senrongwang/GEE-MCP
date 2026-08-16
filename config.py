"""配置加载（config.yaml，设计文档第 38 节）。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULTS: dict[str, Any] = {
    "gee": {"project": "", "auth_timeout": 300},
    "download": {"default_crs": "EPSG:3857", "default_format": "GeoTIFF",
                 "default_max_pixels": 10_000_000_000_000},
    "filesystem": {"default_output": "D:/GEE_Data", "allowed_roots": ["D:/GEE_Data"]},
    "planner": {"direct_download_max_mb": 20, "max_grid_dimension": 9000,
                "export_force_threshold": 3, "max_direct_tiles": 1000,
                "max_direct_request_bytes": 44 * 1024 * 1024},
    "qa": {"min_valid_fraction": 0.01},
    "network": {"timeout": 300, "retry": 3},
    "logging": {"level": "INFO", "redact_secrets": True},
    "drive": {"temp_dir": "D:/GEE_Data/.tmp_drive",
              "delete_remote_after_download": False},
    "catalog": {"db_path": "data/gee_catalog.db",
                "stac_catalog_url": "https://storage.googleapis.com/earthengine-stac/catalog/catalog.json",
                "concurrency": 8,
                "request_timeout": 60,
                "retry": 3,
                "validation_cache_ttl_hours": 1,
                "stale_days": 60},
}


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, data: dict[str, Any], config_path: Path | None = None):
        self.data = data
        self.path = config_path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path) if path else Path(__file__).parent / "config.yaml"
        # 深拷贝，避免 merge 时污染全局默认值
        data = copy.deepcopy(_DEFAULTS)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            data = _deep_merge(data, loaded)
        cfg = cls(data, path)
        cfg._post_process()
        return cfg

    def _post_process(self) -> None:
        # 支持环境变量覆盖（便于不同机器部署）
        env_project = os.environ.get("GEE_PROJECT", "")
        if env_project:
            self.data["gee"]["project"] = env_project
        env_output = os.environ.get("GEE_DEFAULT_OUTPUT", "")
        if env_output:
            self.data["filesystem"]["default_output"] = env_output
            if env_output not in self.data["filesystem"]["allowed_roots"]:
                self.data["filesystem"]["allowed_roots"].append(env_output)

    # ---- 便捷访问 ----
    @property
    def gee_project(self) -> str:
        return str(self.data["gee"].get("project") or "").strip()

    @property
    def allowed_roots(self) -> list[str]:
        return list(self.data["filesystem"].get("allowed_roots") or [])

    @property
    def default_output(self) -> str:
        return str(self.data["filesystem"].get("default_output") or "")

    @property
    def default_crs(self) -> str:
        return str(self.data["download"].get("default_crs") or "EPSG:3857")

    @property
    def default_max_pixels(self) -> int:
        return int(self.data["download"].get("default_max_pixels") or 1e13)

    @property
    def direct_download_max_mb(self) -> int:
        return int(self.data["planner"].get("direct_download_max_mb") or 20)

    @property
    def max_grid_dimension(self) -> int:
        return int(self.data["planner"].get("max_grid_dimension") or 9000)

    @property
    def export_force_threshold(self) -> int:
        return int(self.data["planner"].get("export_force_threshold") or 3)

    @property
    def max_direct_tiles(self) -> int:
        """本地直下分片数保护上限（超过报错，避免 GEE 请求配额被打爆）。"""
        return int(self.data["planner"].get("max_direct_tiles") or 1000)

    @property
    def max_direct_request_bytes(self) -> int:
        """单次 getDownloadURL 请求字节预算（P0-1，按 float64 8B/px 反推分块像素）。

        GEE 实际上限 50331648 字节（48MiB），默认保守取 44MiB。
        """
        return int(self.data["planner"].get("max_direct_request_bytes")
                   or 44 * 1024 * 1024)

    @property
    def qa_min_valid_fraction(self) -> float:
        """QA 内容检查：非零像元占比下限（P1-6，低于视为异常如全 0 输出）。"""
        return float(self.data["qa"].get("min_valid_fraction") or 0.01)

    @property
    def network_timeout(self) -> int:
        return int(self.data["network"].get("timeout") or 300)

    @property
    def network_retry(self) -> int:
        return int(self.data["network"].get("retry") or 3)

    @property
    def drive_temp_dir(self) -> str:
        return str(self.data["drive"].get("temp_dir") or self.default_output)

    # ---- Catalog（数据集发现，设计文档《GEE Dataset Discovery》） ----
    @property
    def catalog_db_path(self) -> str:
        """Catalog SQLite 路径（相对路径按项目根目录解析）。"""
        raw = str(self.data["catalog"].get("db_path") or "data/gee_catalog.db")
        p = Path(raw)
        if p.is_absolute():
            return str(p)
        return str(Path(__file__).parent / p)

    @property
    def catalog_stac_url(self) -> str:
        return str(self.data["catalog"].get("stac_catalog_url")
                   or "https://storage.googleapis.com/earthengine-stac/catalog/catalog.json")

    @property
    def catalog_concurrency(self) -> int:
        return int(self.data["catalog"].get("concurrency") or 8)

    @property
    def catalog_request_timeout(self) -> int:
        return int(self.data["catalog"].get("request_timeout") or 60)

    @property
    def catalog_retry(self) -> int:
        return int(self.data["catalog"].get("retry") or 3)

    @property
    def catalog_validation_cache_ttl_hours(self) -> float:
        return float(self.data["catalog"].get("validation_cache_ttl_hours") or 1)

    @property
    def catalog_stale_days(self) -> int:
        return int(self.data["catalog"].get("stale_days") or 60)

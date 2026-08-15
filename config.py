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
                "export_force_threshold": 3, "max_direct_tiles": 1000},
    "network": {"timeout": 300, "retry": 3},
    "logging": {"level": "INFO", "redact_secrets": True},
    "drive": {"temp_dir": "D:/GEE_Data/.tmp_drive",
              "delete_remote_after_download": False},
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
    def network_timeout(self) -> int:
        return int(self.data["network"].get("timeout") or 300)

    @property
    def network_retry(self) -> int:
        return int(self.data["network"].get("retry") or 3)

    @property
    def drive_temp_dir(self) -> str:
        return str(self.data["drive"].get("temp_dir") or self.default_output)

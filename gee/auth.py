"""GEE 认证与初始化（设计文档第 7 节）。"""

from __future__ import annotations

import threading
from typing import Optional

import ee

from config import Config
from utils.logging import get_logger

logger = get_logger(__name__)

# 全局单例会话
_session: Optional["GeeSession"] = None
_lock = threading.Lock()


class GeeAuthError(RuntimeError):
    """GEE 认证 / 初始化失败。"""


class GeeSession:
    """管理 ee 的认证状态。"""

    def __init__(self, config: Config):
        self.config = config
        self.project = config.gee_project

    # ---- 状态检查 ----
    def is_initialized(self) -> bool:
        try:
            return bool(ee.Initialized())
        except Exception:
            return False

    def credentials_exist(self) -> bool:
        """本地是否存在持久化凭据（~/.config/earthengine/credentials）。"""
        try:
            import os
            creds_path = os.path.join(
                os.path.expanduser("~"), ".config", "earthengine", "credentials"
            )
            return os.path.exists(creds_path)
        except Exception:
            return False

    # ---- 初始化 ----
    def initialize(self) -> None:
        """使用现有凭据初始化。若项目未配置则尝试自动发现。"""
        kwargs: dict = {}
        if self.project:
            kwargs["project"] = self.project
        try:
            ee.Initialize(**kwargs)
            logger.info("Earth Engine 初始化成功 (project=%s)", self.project or "auto")
        except Exception as exc:
            raise GeeAuthError(
                f"Earth Engine 初始化失败（请先运行 gee_login 或检查凭据）: {exc}"
            ) from exc

    def ensure(self) -> None:
        """确保已初始化；未初始化则抛出 GeeAuthError 并提示先登录。"""
        if self.is_initialized():
            return
        if not self.credentials_exist():
            raise GeeAuthError(
                "未找到本地 GEE 凭据，请先调用 gee_login 完成 OAuth 登录。"
            )
        self.initialize()

    # ---- 登录 ----
    def login(self, force: bool = False) -> dict:
        """完整登录流程（设计文档第 7.1 节）：

        AI -> gee_login -> 检查本地凭据 -> 不存在则打开 OAuth -> 保存 credentials
        """
        if not force and self.is_initialized():
            project = self.project or self._current_project()
            return {"authenticated": True, "project": project, "fresh": False}

        if not force and self.credentials_exist():
            # 凭据存在但未初始化：直接初始化
            self.initialize()
            return {"authenticated": True, "project": self.project or "auto", "fresh": False}

        logger.info("开始 GEE OAuth 登录（浏览器将自动打开）...")
        ee.Authenticate()  # 打开浏览器 OAuth 流程
        self.initialize()
        project = self.project or self._current_project()
        logger.info("GEE 登录完成")
        return {"authenticated": True, "project": project, "fresh": True}

    def _current_project(self) -> str:
        try:
            info = ee.data.getProjectConfig() or {}
            project = info.get("projectId") or info.get("name") or ""
            return str(project)
        except Exception:
            return "auto"


def ensure_initialized(config: Optional[Config] = None) -> GeeSession:
    """获取全局 GeeSession 并确保已初始化（供其他模块调用）。"""
    global _session
    cfg = config or Config.load()
    with _lock:
        if _session is None:
            _session = GeeSession(cfg)
        _session.ensure()
        return _session


def login(force: bool = False, config: Optional[Config] = None) -> dict:
    """MCP 工具 gee_login 的底层实现。"""
    cfg = config or Config.load()
    session = GeeSession(cfg)
    return session.login(force=force)

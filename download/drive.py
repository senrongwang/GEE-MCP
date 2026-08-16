"""Google Drive 本地回传下载（设计文档第 20 节）。

复用 earthengine 保存的 OAuth 凭据调用 Drive API；
若凭据 scope 不含 drive 或 google-api-python-client 未安装，
则降级为返回 Drive 网页链接并提示手动下载。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from utils.logging import get_logger

logger = get_logger(__name__)


class DriveDownloadError(RuntimeError):
    """Drive 下载失败。"""


def _credentials_path() -> Optional[Path]:
    for p in (
        Path(os.path.expanduser("~")) / ".config" / "earthengine" / "credentials",
        Path(os.path.expanduser("~")) / ".earthengine",
    ):
        if p.exists():
            return p
    return None


def _load_credentials() -> Optional[dict]:
    path = _credentials_path()
    if not path:
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            return json.loads(raw)
        # 老格式：纯 refresh_token 文本
        return {"refresh_token": raw}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取凭据失败: %s", exc)
        return None


class DriveDownloader:
    """用 GEE 凭据下载 Drive 中 Export 生成的文件。"""

    def __init__(self):
        self._service = None
        self._available = False
        self._error: Optional[str] = None

    def _ensure_service(self):
        if self._service is not None:
            return
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            self._error = "未安装 google-api-python-client（pip install ai-gee-downloader[drive]）"
            return
        creds_data = _load_credentials()
        if not creds_data:
            self._error = "未找到 earthengine 凭据，请先运行 gee_login"
            return
        try:
            creds = Credentials(
                token=None,
                refresh_token=creds_data.get("refresh_token"),
                token_uri=creds_data.get(
                    "token_uri", "https://oauth2.googleapis.com/token"),
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
            # 强制刷新以验证 scope；若原始授权不含 drive 会抛异常
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._error = (
                f"无法使用 GEE 凭据访问 Drive（可能授权范围不含 Drive）: {exc}"
            )

    def find_file(self, folder: str, name: str) -> Optional[dict]:
        """在指定 Drive 文件夹下按文件名（忽略 .tif 后缀）查找文件。"""
        self._ensure_service()
        if not self._available:
            raise DriveDownloadError(self._error or "Drive 不可用")
        try:
            query = (
                f"name contains '{name}' and "
                f"mimeType='image/tiff' and trashed=false"
            )
            results = self._service.files().list(
                q=query, fields="files(id,name,size,mimeType)", pageSize=50
            ).execute()
            files = results.get("files", [])
            for f in files:
                fname = f.get("name", "")
                if fname.startswith(name) or name in fname:
                    return f
            return None
        except Exception as exc:  # noqa: BLE001
            raise DriveDownloadError(f"查询 Drive 文件失败: {exc}") from exc

    def quota(self) -> dict:
        """查询 Google Drive 存储配额（P2-9：export 前置检查空间）。

        返回 {"limit": 总空间, "usage": 已用, "remaining": 剩余}（字节）。
        """
        self._ensure_service()
        if not self._available:
            raise DriveDownloadError(self._error or "Drive 不可用")
        try:
            q = self._service.about().get(fields="storageQuota").execute()
            limit = int(q.get("limit") or 0)
            usage = int(q.get("usageInDrive") or int(q.get("usage") or 0))
            return {
                "limit": limit,
                "usage": usage,
                "remaining": max(0, limit - usage),
            }
        except Exception as exc:  # noqa: BLE001
            raise DriveDownloadError(f"查询 Drive 配额失败: {exc}") from exc

    def download(self, file_id: str, out_path: str | Path) -> Path:
        """下载 Drive 文件到本地路径。"""
        self._ensure_service()
        if not self._available:
            raise DriveDownloadError(self._error or "Drive 不可用")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            request = self._service.files().get_media(fileId=file_id)
            with open(out, "wb") as f:
                from googleapiclient.http import MediaIoBaseDownload
                downloader = MediaIoBaseDownload(f, request, chunksize=8 * 1024 * 1024)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            logger.info("Drive 下载完成: %s -> %s", file_id, out)
            return out
        except Exception as exc:  # noqa: BLE001
            raise DriveDownloadError(f"Drive 文件下载失败: {exc}") from exc

    @property
    def available(self) -> bool:
        if self._service is None:
            self._ensure_service()
        return self._available

    @property
    def error(self) -> Optional[str]:
        return self._error


def drive_web_url(folder: str, name: str) -> str:
    """降级方案：返回 Drive 网页搜索链接，提示用户手动下载。"""
    query = f"{name} in '{folder}'"
    from urllib.parse import quote
    return f"https://drive.google.com/drive/search?q={quote(query)}"

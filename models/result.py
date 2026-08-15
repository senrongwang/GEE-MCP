"""下载结果模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class FileInfo:
    path: str
    size_bytes: int
    bands: int = 0
    width: int = 0
    height: int = 0
    crs: str = ""
    resolution: str = ""
    dtype: str = ""
    nodata: Optional[float] = None
    qa: Optional[dict] = None


@dataclass
class DownloadResult:
    task_id: str
    state: str
    dataset: str = ""
    strategy: Optional[str] = None
    files: list = field(default_factory=list)
    metadata_path: Optional[str] = None
    plan: dict = field(default_factory=dict)
    error: Optional[str] = None
    message: str = ""
    drive_links: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

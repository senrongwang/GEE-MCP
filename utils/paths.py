"""路径安全与输出目录管理（设计文档第 37 节：本地路径白名单）。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from utils.logging import get_logger

logger = get_logger(__name__)

# 非法文件名字符
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class PathNotAllowedError(Exception):
    """目标路径不在配置的白名单根目录内。"""


def _norm(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.path.expandvars(str(path)))))


def ensure_allowed_root(path: str | Path, allowed_roots: Iterable[str]) -> Path:
    """校验 path 位于 allowed_roots 中的某一个根目录内，否则抛出 PathNotAllowedError。"""
    target = _norm(path)
    roots = [_norm(Path(r)) for r in allowed_roots]
    for root in roots:
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    raise PathNotAllowedError(
        f"路径不在允许的根目录内: {target} （允许: {[str(r) for r in roots]}）"
    )


def resolve_output_path(base: str | Path, *parts: str, allowed_roots: Iterable[str]) -> Path:
    """在允许根目录内解析输出路径，自动创建目录。"""
    target = ensure_allowed_root(Path(base, *parts), allowed_roots)
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_filename(name: str, fallback: str = "output") -> str:
    """把任意字符串清洗成安全的文件名（不含路径、不含非法字符）。"""
    cleaned = _UNSAFE_CHARS.sub("_", name).strip(" .")
    cleaned = cleaned[:120].rstrip(" .")
    if not cleaned:
        return fallback
    stem = cleaned.split(".")[0]
    if stem.upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def make_output_dir(base: str | Path, dataset_dir: str, year: str | None = None, *,
                    allowed_roots: Iterable[str]) -> Path:
    """按设计文档第 23 节组织文件：

    D:/GEE_Data/<dataset>/<year>/<date>.tif
    """
    parts = [dataset_dir]
    if year:
        parts.append(year)
    return resolve_output_path(base, *parts, allowed_roots=allowed_roots)

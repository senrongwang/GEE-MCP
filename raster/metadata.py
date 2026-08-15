"""元数据生成（设计文档第 24 节）。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from raster.inspect import RasterInfo, inspect_raster


def build_metadata(
    dataset: str,
    start_date: str,
    end_date: str,
    boundary: str,
    crs: str,
    scale: int,
    fmt: str,
    bands: list[str],
    files: list[dict],
    plan: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> dict:
    """构造 metadata.json 内容。"""
    meta = {
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "boundary": boundary,
        "crs": crs,
        "scale": scale,
        "format": fmt,
        "bands": bands,
        "download_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    if plan:
        meta["plan"] = plan
    if extra:
        meta.update(extra)
    return meta


def write_metadata(
    out_dir: str | Path,
    dataset: str,
    start_date: str,
    end_date: str,
    boundary: str,
    crs: str,
    scale: int,
    fmt: str,
    bands: list[str],
    files: list[str | Path],
    plan: Optional[dict] = None,
) -> Path:
    """把 metadata.json 写入输出目录，并返回路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    file_infos = []
    for f in files:
        p = Path(f)
        info = inspect_raster(p)
        if info.readable:
            file_infos.append({
                "path": str(p),
                "size_bytes": p.stat().st_size if p.exists() else 0,
                "bands": info.bands,
                "width": info.width,
                "height": info.height,
                "crs": info.crs,
                "resolution": info.resolution,
                "dtype": info.dtype,
            })
        else:
            file_infos.append({
                "path": str(p),
                "size_bytes": p.stat().st_size if p.exists() else 0,
                "error": info.error,
            })

    meta = build_metadata(
        dataset=dataset,
        start_date=start_date,
        end_date=end_date,
        boundary=boundary,
        crs=crs,
        scale=scale,
        fmt=fmt,
        bands=bands,
        files=file_infos,
        plan=plan,
    )
    meta_path = out / "metadata.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta_path

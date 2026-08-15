"""Export Task 执行引擎（设计文档第 19 节）。

流程：GEE Export -> Google Drive -> 本地 Downloader -> Local GeoTIFF
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import ee

from gee.export import ExportSpec, start_export
from gee.task import TaskMonitor
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ExportOutcome:
    state: str
    gee_task_id: str
    description: str
    drive_name: str
    drive_folder: str
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "gee_task_id": self.gee_task_id,
            "description": self.description,
            "drive_name": self.drive_name,
            "drive_folder": self.drive_folder,
            "error_message": self.error_message,
        }


def run_export_to_drive(
    image: ee.Image,
    spec: ExportSpec,
    clip: bool = False,
    bands: Optional[list[str]] = None,
    poll_interval_s: float = 15.0,
    timeout_s: Optional[float] = None,
    progress_cb=None,
) -> ExportOutcome:
    """启动 Export 任务并轮询到终态。返回 Outcome 供 Drive 下载。"""
    task = start_export(image, spec, clip=clip, bands=bands)
    monitor = TaskMonitor(poll_interval_s=poll_interval_s, timeout_s=timeout_s)
    status = monitor.wait(task, progress_cb=progress_cb)
    state = status.get("state", "UNKNOWN")
    outcome = ExportOutcome(
        state=state,
        gee_task_id=task.id,
        description=spec.description,
        drive_name=spec.drive_name(),
        drive_folder=spec.folder,
        error_message=status.get("error_message"),
    )
    if state == "COMPLETED":
        logger.info("Export 完成: %s -> Drive/%s/%s.tif",
                    spec.description, spec.folder, spec.drive_name())
    else:
        logger.warning("Export 未完成: %s state=%s err=%s",
                       spec.description, state, outcome.error_message)
    return outcome

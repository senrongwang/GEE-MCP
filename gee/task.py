"""GEE 任务监控（设计文档第 8.5、8.6 节）。"""

from __future__ import annotations

from typing import Optional

import ee

from utils.logging import get_logger

logger = get_logger(__name__)

# GEE 任务状态 -> 本地状态映射
_GEE_TO_LOCAL = {
    "READY": "SUBMITTING",
    "RUNNING": "RUNNING",
    "COMPLETED": "COMPLETED",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
    "CANCEL_REQUESTED": "CANCELLED",
}


def describe_task(task_id: str) -> dict:
    """查询单个 GEE Export Task 的状态（gee_task_status）。"""
    try:
        status = ee.data.getTaskStatus(task_id)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"查询任务状态失败: {exc}") from exc
    if not status:
        raise RuntimeError(f"任务不存在: {task_id}")
    st = status[0]
    return {
        "task_id": st.get("id"),
        "state": st.get("state"),
        "description": st.get("description"),
        "error_message": st.get("error_message"),
        "creation_timestamp_ms": st.get("creation_timestamp_ms"),
        "update_timestamp_ms": st.get("update_timestamp_ms"),
        "progress": None,
    }


def list_export_tasks(status_filter: Optional[str] = None, limit: int = 100) -> list[dict]:
    """列出当前用户的 GEE Export Tasks（gee_list_tasks）。"""
    try:
        tasks = ee.batch.Task.list()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"列出任务失败: {exc}") from exc

    out = []
    for t in tasks[:limit]:
        st = t.status()
        state = st.get("state", "UNKNOWN")
        if status_filter and state != status_filter.upper():
            continue
        out.append({
            "id": t.id,
            "state": state,
            "description": st.get("description", ""),
            "error_message": st.get("error_message"),
            "creation_timestamp_ms": st.get("creation_timestamp_ms"),
        })
    return out


class TaskMonitor:
    """轮询单个 GEE 任务直到终态。"""

    def __init__(self, poll_interval_s: float = 15.0, timeout_s: Optional[float] = None):
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s

    def wait(self, task: ee.batch.Task, progress_cb=None) -> dict:
        import time
        started = time.time()
        while True:
            st = task.status()
            state = st.get("state", "UNKNOWN")
            if progress_cb:
                progress_cb(state)
            if state in ("COMPLETED", "FAILED", "CANCELLED", "CANCEL_REQUESTED"):
                return st
            if self.timeout_s and (time.time() - started) > self.timeout_s:
                st["state"] = "TIMEOUT"
                return st
            time.sleep(self.poll_interval_s)

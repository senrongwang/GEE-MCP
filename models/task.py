"""任务记录与状态机（设计文档第 29 节）。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# 设计文档第 29 节状态机
TASK_FLOW = [
    "PENDING",
    "VALIDATING",
    "PLANNING",
    "SUBMITTING",
    "RUNNING",
    "DOWNLOADING",
    "VALIDATING_OUTPUT",
    "COMPLETED",
]

TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}


class InvalidTransition(ValueError):
    pass


@dataclass
class TaskRecord:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = "PENDING"
    description: str = ""
    dataset: str = ""
    request: dict = field(default_factory=dict)
    gee_task_id: Optional[str] = None
    gee_state: Optional[str] = None
    strategy: Optional[str] = None
    progress: Optional[float] = None
    progress_note: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    submitted_at: Optional[float] = None
    completed_at: Optional[float] = None
    files: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    result: Optional[dict] = None
    drive_links: list = field(default_factory=list)
    # 单调递增序号（列表排序用，避免同一毫秒创建时顺序不稳定）
    seq: int = 0

    def transition(self, new_state: str) -> None:
        """状态机迁移：任何状态 -> FAILED / CANCELLED 均合法。"""
        new_state = new_state.upper()
        if new_state not in TASK_FLOW and new_state not in TERMINAL_STATES:
            raise InvalidTransition(f"未知状态: {new_state}")
        if self.state in TERMINAL_STATES and new_state != self.state:
            raise InvalidTransition(
                f"终态 {self.state} 不能再迁移到 {new_state}"
            )
        self.state = new_state
        self.updated_at = time.time()
        if new_state in ("COMPLETED", "FAILED", "CANCELLED"):
            self.completed_at = time.time()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = _fmt_ts(d.get("created_at"))
        d["updated_at"] = _fmt_ts(d.get("updated_at"))
        d["submitted_at"] = _fmt_ts(d.get("submitted_at"))
        d["completed_at"] = _fmt_ts(d.get("completed_at"))
        return d


def _fmt_ts(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class TaskStore:
    """本地任务持久化存储（JSON 文件，进程重启后仍可查询任务状态）。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"

    def save(self, record: TaskRecord) -> None:
        with self._lock:
            if not record.seq:
                record.seq = self._next_seq()
            self._path(record.task_id).write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _next_seq(self) -> int:
        m = 0
        for p in self.root.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                m = max(m, int(d.get("seq") or 0))
            except Exception:  # noqa: BLE001
                continue
        return m + 1

    def load(self, task_id: str) -> Optional[TaskRecord]:
        p = self._path(task_id)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        rec = TaskRecord(
            task_id=data["task_id"],
            state=data["state"],
            description=data.get("description", ""),
            dataset=data.get("dataset", ""),
            request=data.get("request", {}),
            gee_task_id=data.get("gee_task_id"),
            gee_state=data.get("gee_state"),
            strategy=data.get("strategy"),
            progress=data.get("progress"),
            progress_note=data.get("progress_note"),
            error=data.get("error"),
            files=data.get("files", []),
            plan=data.get("plan", {}),
            result=data.get("result"),
            drive_links=data.get("drive_links", []),
            seq=int(data.get("seq") or 0),
        )
        return rec

    def list(self) -> list[TaskRecord]:
        with self._lock:
            recs = []
            for p in sorted(self.root.glob("*.json")):
                recs.append(self.load(p.stem))
            # 按单调序号倒序（创建顺序）
            recs.sort(key=lambda r: r.seq, reverse=True)
            return recs

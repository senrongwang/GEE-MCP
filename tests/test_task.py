"""单元测试：任务状态机与持久化。"""

import pytest

from models.task import TASK_FLOW, InvalidTransition, TaskRecord, TaskStore


class TestStateMachine:
    def test_flow(self):
        r = TaskRecord()
        for state in TASK_FLOW:
            r.transition(state)
        assert r.state == "COMPLETED"
        assert r.completed_at is not None

    def test_fail_from_any(self):
        r = TaskRecord()
        r.transition("RUNNING")
        r.transition("FAILED")
        assert r.state == "FAILED"

    def test_cancel_from_running(self):
        r = TaskRecord()
        r.transition("RUNNING")
        r.transition("CANCELLED")
        assert r.state == "CANCELLED"

    def test_terminal_frozen(self):
        r = TaskRecord()
        r.transition("COMPLETED")
        with pytest.raises(InvalidTransition):
            r.transition("RUNNING")

    def test_unknown_state(self):
        r = TaskRecord()
        with pytest.raises(InvalidTransition):
            r.transition("BOGUS")


class TestTaskStore:
    def test_save_load_roundtrip(self, tmp_path):
        store = TaskStore(tmp_path)
        r = TaskRecord(description="MODIS_2021", dataset="MODIS/061/MOD13Q1")
        r.request = {"dataset": "MODIS/061/MOD13Q1"}
        r.transition("RUNNING")
        store.save(r)

        loaded = store.load(r.task_id)
        assert loaded is not None
        assert loaded.task_id == r.task_id
        assert loaded.state == "RUNNING"
        assert loaded.description == "MODIS_2021"
        assert loaded.dataset == "MODIS/061/MOD13Q1"

    def test_missing(self, tmp_path):
        store = TaskStore(tmp_path)
        assert store.load("nope") is None

    def test_list_sorted(self, tmp_path):
        store = TaskStore(tmp_path)
        a = TaskRecord(description="a")
        store.save(a)
        b = TaskRecord(description="b")
        store.save(b)
        tasks = store.list()
        assert len(tasks) == 2
        # 最新创建的在前（按单调序号）
        assert tasks[0].task_id == b.task_id

    def test_persist_completed_files(self, tmp_path):
        store = TaskStore(tmp_path)
        r = TaskRecord(description="t")
        r.files = [{"path": "x.tif", "qa": {"passed": True}}]
        r.transition("COMPLETED")
        store.save(r)
        loaded = store.load(r.task_id)
        assert loaded.files == [{"path": "x.tif", "qa": {"passed": True}}]

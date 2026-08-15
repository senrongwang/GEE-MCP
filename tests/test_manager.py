"""单元测试：配置加载与下载管理器（不依赖 GEE，用桩替换 GEE 调用）。"""

import pytest

from config import Config
from models.request import DownloadRequest
from models.task import TaskRecord


class TestConfig:
    def test_defaults(self, tmp_path):
        cfg = Config.load()
        assert cfg.default_crs == "EPSG:3857"
        assert cfg.default_max_pixels == 10_000_000_000_000
        assert cfg.direct_download_max_mb > 0
        assert cfg.max_grid_dimension > 0

    def test_custom_yaml(self, tmp_path):
        import yaml
        p = tmp_path / "cfg.yaml"
        p.write_text(yaml.safe_dump({
            "download": {"default_crs": "EPSG:4326"},
            "filesystem": {"allowed_roots": [str(tmp_path)]},
        }), encoding="utf-8")
        cfg = Config.load(p)
        assert cfg.default_crs == "EPSG:4326"
        assert str(tmp_path) in cfg.allowed_roots
        # 未覆盖的仍为默认
        assert cfg.direct_download_max_mb == 20


class TestManagerNoGEE:
    """不触发 GEE 调用的管理器行为。"""

    def test_local_first_strategy(self, tmp_path):
        """默认策略：即使分片很多 / 影像很多，也永不自动 Export（本地优先）。"""
        from download.manager import DownloadManager
        from planner.download_planner import STRATEGY_DIRECT, STRATEGY_EXPORT
        from models.request import DownloadRequest
        cfg = Config.load()
        cfg.data["filesystem"]["default_output"] = str(tmp_path)
        manager = DownloadManager(cfg)
        req = DownloadRequest(
            dataset="MODIS/061/MOD13A2",
            start_date="2021-01-01",
            end_date="2021-12-31",
            boundary="projects/x/assets/CUS",
            output=str(tmp_path),
        )
        # auto / direct：始终本地直下
        assert manager._decide_strategy(req, 100, 500, 99999, 20000) == STRATEGY_DIRECT
        assert manager._decide_strategy(req, 3, 1, 1, 10) == STRATEGY_DIRECT
        # 显式 export：才走远程
        req2 = DownloadRequest(
            dataset="MODIS/061/MOD13A2",
            start_date="2021-01-01",
            end_date="2021-12-31",
            boundary="projects/x/assets/CUS",
            output=str(tmp_path),
            strategy="export",
        )
        assert manager._decide_strategy(req2, 1, 1, 1, 1) == STRATEGY_EXPORT

    def test_submit_rejects_invalid(self, tmp_path):
        from download.manager import DownloadManager
        cfg = Config.load()
        cfg.data["filesystem"]["default_output"] = str(tmp_path)
        manager = DownloadManager(cfg)
        req = DownloadRequest(
            dataset="",  # 非法
            start_date="2021-01-01",
            end_date="2021-12-31",
            boundary="projects/x/assets/Anhui",
            output=str(tmp_path),
        )
        with pytest.raises(Exception):
            manager.submit(req)

    def test_status_missing(self, tmp_path):
        from download.manager import DownloadManager
        cfg = Config.load()
        cfg.data["filesystem"]["default_output"] = str(tmp_path)
        manager = DownloadManager(cfg)
        assert manager.status("does-not-exist") is None

    def test_list_empty(self, tmp_path):
        from download.manager import DownloadManager
        cfg = Config.load()
        cfg.data["filesystem"]["default_output"] = str(tmp_path)
        manager = DownloadManager(cfg)
        assert manager.list_tasks() == []


class TestDailyTasks:
    """_daily_tasks：daily 模式任务构造（半开区间 + 键去重，不依赖 GEE）。

    回归：filterDate 是半开区间 [start, end)，单日区间必须取 [day, day+1)，
    否则 start == end 返回空集，所有时间片被跳过。
    """

    def test_half_open_interval(self):
        from datetime import date
        from download.manager import _daily_tasks
        from gee.collection import ImageItem
        images = [ImageItem(id="a", date="2021-01-01")]
        tasks = _daily_tasks(images, None)
        assert tasks == [("2021-01-01", date(2021, 1, 1), date(2021, 1, 2), "mean", None)]

    def test_dedup_same_day(self):
        from datetime import timedelta
        from download.manager import _daily_tasks
        from gee.collection import ImageItem
        images = [
            ImageItem(id="a", date="2021-01-01"),
            ImageItem(id="b", date="2021-01-01"),
            ImageItem(id="c", date="2021-01-02"),
        ]
        tasks = _daily_tasks(images, "median")
        assert [t[0] for t in tasks] == ["2021-01-01", "2021-01-02"]
        assert [t[3] for t in tasks] == ["median", "median"]
        for key, pstart, pend, _, _ in tasks:
            assert pend - pstart == timedelta(days=1)
            assert pstart.isoformat() == key

    def test_many_days_not_capped_by_helper(self):
        """daily 模式允许超过逐景上限的天数（上限拦截属于执行层策略，helper 不截断）。"""
        from download.manager import _daily_tasks
        from gee.collection import ImageItem
        images = [ImageItem(id=str(i), date=f"2021-{i:02d}-01") for i in range(1, 13)]
        assert len(_daily_tasks(images, None)) == 12

    def test_empty_dates_ignored(self):
        from download.manager import _daily_tasks
        from gee.collection import ImageItem
        images = [ImageItem(id="a", date=""), ImageItem(id="b", date="2021-01-01")]
        tasks = _daily_tasks(images, None)
        assert [t[0] for t in tasks] == ["2021-01-01"]

    def test_empty(self):
        from download.manager import _daily_tasks
        assert _daily_tasks([], None) == []


class TestStackPeriodOutputs:
    """_stack_period_outputs：多时间片合并为一个多波段 tif（不依赖 GEE）。"""

    def _manager(self, tmp_path):
        from download.manager import DownloadManager
        cfg = Config.load()
        cfg.data["filesystem"]["default_output"] = str(tmp_path)
        return DownloadManager(cfg)

    def _tif(self, path, value):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        arr = np.full((1, 10, 10), value, dtype=np.float32)
        with rasterio.open(
            str(path), "w", driver="GTiff",
            height=10, width=10, count=1, dtype="float32",
            crs="EPSG:3857", transform=from_origin(0, 100000, 1000, 1000),
        ) as dst:
            dst.write(arr)
        return path

    def test_stack_two_periods(self, tmp_path):
        manager = self._manager(tmp_path)
        dataset_dir = tmp_path / "MODIS_061_MOD13Q1"
        p1 = self._tif(tmp_path / "raw1.tif", 1.0)
        p2 = self._tif(tmp_path / "raw2.tif", 2.0)
        req = DownloadRequest(
            dataset="MODIS/061/MOD13Q1",
            start_date="2021-01-01",
            end_date="2021-01-02",
            boundary="projects/x/assets/CUS",
            output=str(tmp_path),
            stack_periods=True,
        ).validate()
        out = manager._stack_period_outputs(
            record=None, request=req,
            plan={"bands": ["NDVI"]},
            period_outputs=[
                {"path": str(p1), "period": "2021-01-01"},
                {"path": str(p2), "period": "2021-01-02"},
            ],
            dataset_dir=dataset_dir,
            stack_tmp=tmp_path / "stack_tmp",
        )
        assert len(out) == 1
        assert out[0]["stacked"] is True
        assert out[0]["stacked_band_count"] == 2
        import rasterio
        with rasterio.open(out[0]["path"]) as ds:
            assert ds.count == 2
            assert ds.descriptions[0] == "NDVI_2021-01-01"
            assert ds.descriptions[1] == "NDVI_2021-01-02"

    def test_stack_single_period_fallback(self, tmp_path):
        manager = self._manager(tmp_path)
        dataset_dir = tmp_path / "MODIS_061_MOD13Q1"
        p1 = self._tif(tmp_path / "raw1.tif", 1.0)
        req = DownloadRequest(
            dataset="MODIS/061/MOD13Q1",
            start_date="2021-01-01",
            end_date="2021-01-01",
            boundary="projects/x/assets/CUS",
            output=str(tmp_path),
            stack_periods=True,
        ).validate()
        out = manager._stack_period_outputs(
            record=None, request=req,
            plan={"bands": ["NDVI"]},
            period_outputs=[{"path": str(p1), "period": "2021-01-01"}],
            dataset_dir=dataset_dir,
            stack_tmp=tmp_path / "stack_tmp",
        )
        assert len(out) == 1
        assert "stacked" not in out[0]  # 退化：单文件直接改名

    def test_stack_empty_raises(self, tmp_path):
        manager = self._manager(tmp_path)
        req = DownloadRequest(
            dataset="MODIS/061/MOD13Q1",
            start_date="2021-01-01",
            end_date="2021-01-02",
            boundary="projects/x/assets/CUS",
            output=str(tmp_path),
            stack_periods=True,
        ).validate()
        with pytest.raises(Exception):
            manager._stack_period_outputs(
                record=None, request=req,
                plan={"bands": ["NDVI"]},
                period_outputs=[],
                dataset_dir=tmp_path / "x",
                stack_tmp=None,
            )

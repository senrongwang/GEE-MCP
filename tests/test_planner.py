"""单元测试：规模估算与时间规划（不依赖 GEE）。"""

from datetime import date

from gee.collection import ImageItem
from planner.size_estimator import (
    estimate_grid_dimension,
    estimate_pixels,
    estimate_raster_size,
)
from planner.temporal_planner import TemporalPlanner


class TestSizeEstimator:
    def test_pixels(self):
        # 1 km x 1 km @ 1 km = 1 像元
        assert estimate_pixels(1_000_000, 1000) == 1
        # 100 km x 100 km @ 1 km = 10000 像元
        assert estimate_pixels(100_000 * 100_000, 1000) == 10000

    def test_grid_dimension(self):
        assert estimate_grid_dimension(10000) == 100
        assert estimate_grid_dimension(1) == 1

    def test_size_estimation(self):
        est = estimate_raster_size(100_000 * 100_000, 1000, band_count=2, dtype="FLOAT32")
        assert est.pixel_count == 10000
        assert est.grid_dimension == 100
        # 10000 px * 4B * 2 bands
        assert est.bytes_total == 80_000
        assert est.band_count == 2

    def test_direct_threshold_crossed(self):
        # 100 km x 100 km @ 30 m -> 1111 万像元 -> 远超 20MB
        est = estimate_raster_size(100_000 * 100_000, 30, band_count=1, dtype="FLOAT32")
        assert est.mb_total > 20


def _images(dates):
    return [ImageItem(id=f"img{i}", date=d) for i, d in enumerate(dates)]


class TestTemporalPlanner:
    def test_few_images_daily(self):
        images = _images(["2021-01-01", "2021-01-05", "2021-02-01"])
        plan = TemporalPlanner().plan(images, date(2021, 1, 1), date(2021, 12, 31), None)
        assert plan.strategy == "daily"
        assert plan.tasks == 3

    def test_many_images_monthly(self):
        # 90 景跨 1 年 -> monthly
        images = _images([f"2021-{m:02d}-01" for m in range(1, 13) for _ in range(5)])
        plan = TemporalPlanner().plan(images, date(2021, 1, 1), date(2021, 12, 31), None)
        assert plan.strategy == "monthly"
        assert plan.tasks == 12

    def test_aggregation_groups(self):
        images = _images([f"2021-{m:02d}-01" for m in range(1, 13)])
        plan = TemporalPlanner().plan(images, date(2021, 1, 1), date(2021, 12, 31), "mean")
        assert plan.tasks == 12

    def test_empty(self):
        plan = TemporalPlanner().plan([], date(2021, 1, 1), date(2021, 12, 31), None)
        assert plan.image_count == 0
        assert plan.tasks == 0

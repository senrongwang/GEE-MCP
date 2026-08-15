"""单元测试：Ranking 打分函数（设计文档第 10、16 节）。"""

from datetime import date

import pytest

from catalog.ranking import (
    WEIGHTS,
    band_score,
    build_match_reasons,
    date_coverage_score,
    keyword_score,
    platform_score,
    region_score,
    resolution_score,
    temporal_score,
    total_score,
)
from models.dataset import BandInfo, DatasetRecord


def _bands(specs):
    """把 ["EVI"] / [{"name": "EVI"}] 转为 BandInfo 列表。"""
    out = []
    for s in specs:
        if isinstance(s, str):
            out.append(BandInfo(name=s))
        else:
            out.append(BandInfo(**{k: v for k, v in s.items() if k in
                                   ("name", "description", "units", "dtype",
                                    "scale", "offset", "valid_min", "valid_max")}))
    return out


def _rec(**kw):
    base = dict(
        id="TEST/1", name="Test Dataset", type="ImageCollection",
        spatial_resolution=250, temporal_resolution="16-day",
        start_date="2000-01-01", end_date="2025-12-31",
        bands=[{"name": "EVI"}, {"name": "NDVI"}],
        tags=["evi", "vegetation"],
    )
    base.update(kw)
    bands = _bands(base.pop("bands"))
    return DatasetRecord(**base, bands=bands)


class TestResolutionScore:
    def test_exact(self):
        assert resolution_score(1000, 1000) == 1.0

    def test_factors_of_two(self):
        # 500m vs 1000m -> 0.5；2000m vs 1000m -> 0.5
        assert resolution_score(500, 1000) == pytest.approx(0.5)
        assert resolution_score(2000, 1000) == pytest.approx(0.5)

    def test_far(self):
        assert resolution_score(5000, 1000) < resolution_score(2000, 1000)
        assert resolution_score(250, 1000) > resolution_score(5000, 1000)

    def test_missing(self):
        assert resolution_score(None, 1000) == 0.0
        assert resolution_score(250, None) == 0.0


class TestTemporalScore:
    def test_ladder(self):
        # daily 更接近“日尺度”需求
        assert temporal_score("daily", "daily") == 1.0
        assert temporal_score("8-day", "daily") == 0.8
        assert temporal_score("16-day", "daily") == 0.6
        assert temporal_score("monthly", "daily") == 0.4
        assert temporal_score("annual", "daily") == 0.2

    def test_exact_other(self):
        assert temporal_score("monthly", "monthly") == 1.0

    def test_finer_than_preferred(self):
        # 要求 monthly，数据是 daily：更细仍可接受
        assert temporal_score("daily", "monthly") == 0.9

    def test_missing(self):
        assert temporal_score(None, "daily") == 0.0
        assert temporal_score("daily", None) == 0.0


class TestDateCoverage:
    def test_full(self):
        rec = _rec(start_date="2000-01-01", end_date="2025-12-31")
        s, st = date_coverage_score(rec, date(2017, 1, 1), date(2021, 12, 31))
        assert st == "FULL" and s == 1.0

    def test_none(self):
        rec = _rec(start_date="2000-01-01", end_date="2005-12-31")
        s, st = date_coverage_score(rec, date(2017, 1, 1), date(2021, 12, 31))
        assert st == "NONE" and s == 0.0

    def test_partial(self):
        rec = _rec(start_date="2000-01-01", end_date="2018-12-31")
        s, st = date_coverage_score(rec, date(2017, 1, 1), date(2021, 12, 31))
        assert st == "PARTIAL" and s == 0.5


class TestKeyword:
    def test_band_exact(self):
        rec = _rec(bands=[{"name": "EVI"}])
        assert keyword_score(rec, ["EVI"]) == 1.0

    def test_name(self):
        rec = _rec(name="MOD13Q1 Vegetation Indices")
        assert keyword_score(rec, ["mod13q1"]) == 0.8

    def test_desc(self):
        rec = _rec(description="provides vegetation index values")
        assert keyword_score(rec, ["vegetation"]) == 0.6

    def test_tag(self):
        rec = _rec(tags=["vegetation", "modis"])
        assert keyword_score(rec, ["modis"]) == 0.4

    def test_no_match(self):
        assert keyword_score(_rec(), ["zzzz"]) == 0.0


class TestBandScore:
    def test_all(self):
        rec = _rec(bands=[{"name": "EVI"}, {"name": "NDVI"}])
        assert band_score(rec, ["EVI", "NDVI"]) == 1.0

    def test_partial(self):
        rec = _rec(bands=[{"name": "EVI"}])
        assert band_score(rec, ["EVI", "NDVI"]) == 0.5

    def test_none_requested(self):
        assert band_score(_rec(), None) == 0.0


class TestPlatformRegion:
    def test_platform(self):
        rec = _rec(platform="Terra")
        assert platform_score(rec, ["terra"]) == 1.0
        assert platform_score(rec, ["aqua"]) == 0.0

    def test_region_global_coverage(self):
        rec = _rec(coverage="global")
        assert region_score(rec, "China") == 1.0

    def test_region_bbox(self):
        rec = _rec(bbox=[100.0, 20.0, 120.0, 40.0], coverage="China")
        assert region_score(rec, "China") == 1.0
        assert region_score(rec, "Europe") == 0.0

    def test_region_global_no_bonus(self):
        assert region_score(_rec(), "global") == 0.0


class TestTotal:
    def test_weighted_average(self):
        rec = _rec(spatial_resolution=1000, temporal_resolution="daily")
        s = total_score(
            rec,
            terms=["EVI"],
            requested_bands=["EVI"],
            preferred_resolution=1000,
            preferred_temporal="daily",
            req_start=date(2017, 1, 1),
            req_end=date(2021, 12, 31),
        )
        assert 0.0 <= s <= 1.0
        assert s > 0.9

    def test_no_filters_zero(self):
        assert total_score(_rec()) == 0.0

    def test_weights_positive(self):
        assert all(w > 0 for w in WEIGHTS.values())


class TestMatchReasons:
    def test_reasons(self):
        rec = _rec(spatial_resolution=1000, temporal_resolution="daily",
                   coverage="global")
        reasons = build_match_reasons(
            rec,
            terms=["EVI"],
            requested_bands=["EVI"],
            preferred_resolution=1000,
            preferred_temporal="daily",
            req_start=date(2017, 1, 1),
            req_end=date(2021, 12, 31),
            region="China",
        )
        joined = " ".join(reasons)
        assert "EVI band available" in joined
        assert "空间分辨率" in joined
        assert "daily" in joined
        assert "完整覆盖" in joined
        assert "Global coverage" in joined

    def test_partial_marked(self):
        rec = _rec(start_date="2000-01-01", end_date="2018-12-31")
        reasons = build_match_reasons(
            rec, req_start=date(2017, 1, 1), req_end=date(2021, 12, 31))
        assert any("PARTIAL COVERAGE" in r for r in reasons)

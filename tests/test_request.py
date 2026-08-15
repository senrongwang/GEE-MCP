"""单元测试：请求模型校验。"""

import pytest

from models.request import DownloadRequest, RequestValidationError


def _req(**kw):
    base = dict(
        dataset="MODIS/061/MOD13Q1",
        start_date="2021-01-01",
        end_date="2021-12-31",
        boundary="projects/xxx/assets/Anhui",
        output="D:/GEE_Data",
    )
    base.update(kw)
    return DownloadRequest(**base)


class TestRequestValidation:
    def test_valid(self):
        r = _req(scale="1km", crs="EPSG:3857").validate()
        assert r.scale_m == 1000
        assert r.crs == "EPSG:3857"
        assert r.date_start.isoformat() == "2021-01-01"

    def test_scale_variants(self):
        assert _req(scale="250m").validate().scale_m == 250
        assert _req(scale=9000).validate().scale_m == 9000

    def test_missing_dataset(self):
        with pytest.raises(RequestValidationError):
            _req(dataset="").validate()

    def test_missing_boundary(self):
        with pytest.raises(RequestValidationError):
            _req(boundary="").validate()

    def test_bad_dates(self):
        with pytest.raises(RequestValidationError):
            _req(start_date="2021-12-31", end_date="2021-01-01").validate()

    def test_bad_crs(self):
        with pytest.raises(RequestValidationError):
            _req(crs="WGS84").validate()

    def test_bad_format(self):
        with pytest.raises(RequestValidationError):
            _req(format="PNG").validate()

    def test_bad_time_mode(self):
        with pytest.raises(RequestValidationError):
            _req(time_mode="weekly").validate()

    def test_bad_aggregation(self):
        with pytest.raises(RequestValidationError):
            _req(aggregation="average").validate()

    def test_bad_strategy(self):
        with pytest.raises(RequestValidationError):
            _req(strategy="whatever").validate()

    def test_empty_output(self):
        with pytest.raises(RequestValidationError):
            _req(output="").validate()

    def test_auto_description(self):
        r = _req().validate()
        assert "MODIS_061_MOD13Q1" in r.description

    def test_stack_periods_default_false(self):
        r = _req().validate()
        assert r.stack_periods is False

    def test_stack_periods_true(self):
        r = _req(stack_periods=True).validate()
        assert r.stack_periods is True
        plain = r.to_plain()
        assert plain["stack_periods"] is True

    def test_stack_periods_in_plain(self):
        plain = _req().validate().to_plain()
        assert "stack_periods" in plain
        assert plain["stack_periods"] is False

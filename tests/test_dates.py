"""单元测试：日期与分辨率解析。"""

import pytest

from utils.dates import (
    DateRangeError,
    ScaleError,
    iter_periods,
    parse_date,
    parse_scale,
    period_key,
    validate_date_range,
)


class TestParseScale:
    @pytest.mark.parametrize("value,expected", [
        (1000, 1000),
        (9000, 9000),
        ("1000", 1000),
        ("250m", 250),
        ("500m", 500),
        ("1km", 1000),
        ("9km", 9000),
        ("30m", 30),
        ("1.5km", 1500),
        ("1000 M", 1000),
        (" 250 m ", 250),
    ])
    def test_valid(self, value, expected):
        assert parse_scale(value) == expected

    @pytest.mark.parametrize("bad", ["abc", "", "-5", "0", "km", "10 miles", "1km5"])
    def test_invalid(self, bad):
        with pytest.raises(ScaleError):
            parse_scale(bad)

    def test_zero_raises(self):
        with pytest.raises(ScaleError):
            parse_scale(0)


class TestDates:
    def test_parse_variants(self):
        assert parse_date("2021-01-01").isoformat() == "2021-01-01"
        assert parse_date("20210101").isoformat() == "2021-01-01"
        assert parse_date("2021-01").isoformat() == "2021-01-01"

    def test_validate_range_ok(self):
        s, e = validate_date_range("2021-01-01", "2021-12-31")
        assert s.isoformat() == "2021-01-01"
        assert e.isoformat() == "2021-12-31"

    def test_validate_range_reversed(self):
        with pytest.raises(DateRangeError):
            validate_date_range("2021-12-31", "2021-01-01")

    def test_period_key(self):
        from datetime import date
        d = date(2021, 3, 15)
        assert period_key(d, "native") == "2021-03-15"
        assert period_key(d, "daily") == "2021-03-15"
        assert period_key(d, "monthly") == "2021-03"
        assert period_key(d, "annual") == "2021"

    def test_iter_daily(self):
        from datetime import date
        periods = list(iter_periods(date(2021, 1, 1), date(2021, 1, 3), "daily"))
        assert [k for k, _, _ in periods] == ["2021-01-01", "2021-01-02", "2021-01-03"]
        # period_end 必须是半开区间终点 [day, day+1)，否则 filterDate 单日返回空集
        assert [(s, e) for _, s, e in periods] == [
            (date(2021, 1, 1), date(2021, 1, 2)),
            (date(2021, 1, 2), date(2021, 1, 3)),
            (date(2021, 1, 3), date(2021, 1, 4)),
        ]

    def test_iter_daily_native_same_bounds(self):
        from datetime import date
        assert list(iter_periods(date(2021, 1, 1), date(2021, 1, 1), "native")) == [
            ("2021-01-01", date(2021, 1, 1), date(2021, 1, 2))
        ]

    def test_iter_monthly(self):
        from datetime import date
        keys = [k for k, _, _ in iter_periods(date(2021, 11, 15), date(2022, 1, 10), "monthly")]
        assert keys == ["2021-11", "2021-12", "2022-01"]
        # 半开区间终点：下月初；覆盖整月（含月末最后一天）
        assert list(iter_periods(date(2021, 1, 1), date(2021, 1, 31), "monthly")) == [
            ("2021-01", date(2021, 1, 1), date(2021, 2, 1))
        ]

    def test_iter_annual(self):
        from datetime import date
        keys = [k for k, _, _ in iter_periods(date(2017, 1, 1), date(2021, 12, 31), "annual")]
        assert keys == [str(y) for y in range(2017, 2022)]
        # 半开区间终点：下年初；覆盖整年（含 12-31）
        assert list(iter_periods(date(2021, 1, 1), date(2021, 12, 31), "annual")) == [
            ("2021", date(2021, 1, 1), date(2022, 1, 1))
        ]

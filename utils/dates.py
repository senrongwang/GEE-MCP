"""日期与分辨率解析工具（设计文档第 12、14 节）。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterator

_DAY = timedelta(days=1)


class DateRangeError(ValueError):
    """日期范围非法。"""


class ScaleError(ValueError):
    """分辨率字符串无法解析。"""


def parse_date(value: str | date | datetime) -> date:
    """解析 YYYY-MM-DD / YYYYMMDD / YYYY-MM / 日期对象。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise DateRangeError(f"无法解析日期: {value!r}，请使用 YYYY-MM-DD 格式")


def validate_date_range(start_date: str | date, end_date: str | date) -> tuple[date, date]:
    """校验日期范围并返回 (start, end)。"""
    start = parse_date(start_date)
    end = parse_date(end_date)
    if end < start:
        raise DateRangeError(f"end_date 早于 start_date: {start} > {end}")
    return start, end


_SCALE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(m|km|meter|meters|metre|metres)?\s*$",
    re.IGNORECASE,
)


def parse_scale(value: str | int | float) -> int:
    """把 '250m' / '1km' / '9000' / 1000 解析为米。

    >>> parse_scale("1km")
    1000
    >>> parse_scale("250m")
    250
    >>> parse_scale(9000)
    9000
    """
    if isinstance(value, (int, float)):
        scale = float(value)
    else:
        m = _SCALE_RE.match(str(value))
        if not m:
            raise ScaleError(f"无法解析分辨率: {value!r}，请使用如 '250m' / '1km' / 9000")
        scale = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit == "km":
            scale *= 1000.0
    if scale <= 0:
        raise ScaleError(f"分辨率必须为正数: {value!r}")
    return int(round(scale))


def period_key(d: date, mode: str) -> str:
    """按时间模式返回分组键。

    native -> 每天一景（ISO 日期）
    daily  -> 每天
    monthly-> YYYY-MM
    annual -> YYYY
    """
    mode = mode.lower()
    if mode in ("native", "daily"):
        return d.isoformat()
    if mode == "monthly":
        return f"{d.year:04d}-{d.month:02d}"
    if mode == "annual":
        return f"{d.year:04d}"
    raise ValueError(f"未知时间模式: {mode!r}（支持 native/daily/monthly/annual）")


def iter_periods(start: date, end: date, mode: str) -> Iterator[tuple[str, date, date]]:
    """迭代 [start, end] 按模式切分出的时间段，返回 (key, period_start, period_end)。

    period_end 是 GEE filterDate 半开区间 [period_start, period_end) 的终点（不含该日），
    直接传给 filterDate 即可覆盖整个时间段（含 end 当天）：

    - daily / native -> [day, day+1)
    - monthly        -> [月初, 下月初)
    - annual         -> [年初, 下年初)
    """
    mode = mode.lower()
    if mode in ("native", "daily"):
        cur = start
        while cur <= end:
            yield cur.isoformat(), cur, cur + _DAY
            cur += _DAY
        return
    if mode == "monthly":
        cur = start.replace(day=1)
        while cur <= end:
            nxt = (cur.replace(year=cur.year + 1, month=1) if cur.month == 12
                   else cur.replace(month=cur.month + 1))
            yield f"{cur.year:04d}-{cur.month:02d}", cur, nxt
            cur = nxt
        return
    if mode == "annual":
        cur = start.replace(month=1, day=1)
        while cur <= end:
            nxt = cur.replace(year=cur.year + 1)
            yield f"{cur.year:04d}", cur, nxt
            cur = nxt
        return
    raise ValueError(f"未知时间模式: {mode!r}（支持 native/daily/monthly/annual）")

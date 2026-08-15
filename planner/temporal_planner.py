"""时间规划器：避免机械创建海量任务（设计文档第 22 节）。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from gee.collection import ImageItem
from utils.dates import period_key

# 建议策略
STRATEGY_DAILY = "daily"
STRATEGY_MONTHLY = "monthly"
STRATEGY_ANNUAL = "annual"
STRATEGY_MULTI_YEAR = "multi_year"


@dataclass
class TemporalPlan:
    image_count: int
    spans_years: int
    strategy: str
    tasks: int
    per_period_counts: dict = field(default_factory=dict)
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "image_count": self.image_count,
            "spans_years": self.spans_years,
            "strategy": self.strategy,
            "tasks": self.tasks,
            "per_period_counts": self.per_period_counts,
            "recommendation": self.recommendation,
        }


class TemporalPlanner:
    """根据影像数量与时间跨度选择导出策略：

    A. 每天一个 GeoTIFF（daily）
    B. 每月一个 GeoTIFF（monthly）
    C. 每年一个多波段 GeoTIFF（annual）
    D. 多年一个多波段 GeoTIFF（multi_year）
    """

    # 逐景导出上限（设计示例：MODIS NDVI 一年约 23 景可逐景导出）
    DAILY_MAX_IMAGES = 12
    # 按月分组时每年影像数上限（超过则按年分组）
    MONTHLY_MAX_IMAGES_PER_YEAR = 96
    # 多年合并时单任务影像数上限
    MULTI_YEAR_MAX_IMAGES_PER_TASK = 64

    def plan(self, images: list[ImageItem], start: date, end: date,
             aggregation: Optional[str] = None) -> TemporalPlan:
        n = len(images)
        if n == 0:
            return TemporalPlan(0, 0, STRATEGY_DAILY, 0, {}, "没有影像")
        years = set()
        for it in images:
            if it.date:
                try:
                    years.add(int(it.date[:4]))
                except ValueError:
                    pass
        if not years:
            years = {start.year, end.year}
        spans = max(years) - min(years) + 1
        per_day = Counter(it.date for it in images)
        per_month = Counter(it.date[:7] for it in images)
        per_year = Counter(it.date[:4] for it in images)

        # 无聚合：逐景导出
        if aggregation is None:
            if n <= self.DAILY_MAX_IMAGES:
                rec = STRATEGY_DAILY
                tasks = n
            elif n <= self.MONTHLY_MAX_IMAGES_PER_YEAR * spans:
                rec = STRATEGY_MONTHLY
                tasks = len(per_month)
            else:
                rec = STRATEGY_ANNUAL
                tasks = len(per_year)
            return TemporalPlan(
                image_count=n,
                spans_years=spans,
                strategy=rec,
                tasks=tasks,
                per_period_counts={
                    "daily": dict(per_day),
                    "monthly": dict(per_month),
                    "yearly": dict(per_year),
                },
                recommendation=_recommend(rec, n, spans),
            )

        # 有聚合：每个时间片一个任务
        mode = _aggregation_mode(aggregation)
        counts = per_year if mode == "annual" else per_month
        tasks = len(counts)
        return TemporalPlan(
            image_count=n,
            spans_years=spans,
            strategy=mode,
            tasks=tasks,
            per_period_counts=dict(counts),
            recommendation=(
                f"按{mode}聚合为 {tasks} 个时间片，每个时间片输出 1 个 GeoTIFF"
            ),
        )


def _aggregation_mode(aggregation: str) -> str:
    agg = aggregation.lower()
    if agg in ("mean", "median", "mosaic", "min", "max", "sum", "best", "first"):
        return "monthly"  # 默认按月聚合；后续可扩展
    return "monthly"


def _recommend(strategy: str, n: int, spans: int) -> str:
    if strategy == STRATEGY_DAILY:
        return f"影像数量较少（{n} 景），逐景导出 {n} 个 GeoTIFF"
    if strategy == STRATEGY_MONTHLY:
        return f"影像较多（{n} 景 / {spans} 年），按月导出多波段 GeoTIFF，控制任务数量"
    return f"影像很多（{n} 景 / {spans} 年），按年导出多波段 GeoTIFF，避免任务爆炸"


def build_temporal_plan(
    images: list[ImageItem],
    start: date,
    end: date,
    aggregation: Optional[str] = None,
) -> TemporalPlan:
    return TemporalPlanner().plan(images, start, end, aggregation)

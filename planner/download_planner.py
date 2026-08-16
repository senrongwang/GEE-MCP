"""Download Planner：根据任务规模自动选择下载方式（设计文档第 6.2、17 节）。

小数据 -> Image.getDownloadURL() 直接下载
大数据 -> ee.batch.Export Task 异步导出
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import ee

from config import Config
from planner.size_estimator import SizeEstimate, estimate_raster_size_grid
from utils.logging import get_logger

logger = get_logger(__name__)

STRATEGY_DIRECT = "direct"
STRATEGY_EXPORT = "export"


@dataclass
class PlanResult:
    strategy: str
    reason: str
    estimate: Optional[SizeEstimate] = None
    task_count: int = 1

    def to_dict(self) -> dict:
        d = {
            "strategy": self.strategy,
            "reason": self.reason,
            "task_count": self.task_count,
        }
        if self.estimate:
            d["estimate"] = self.estimate.to_dict()
        return d


class DownloadPlanner:
    """根据估算规模决定 direct / export。

    阈值（保守，见设计文档第 38 节）：
    - direct_download_max_mb：默认 20 MB（分块请求上限按 float64 计约 48MiB，P0-1）
    - max_grid_dimension：默认 9000（GEE 上限 10000）
    """

    def __init__(self, config: Config):
        self.config = config

    def plan(
        self,
        region: ee.Geometry,
        scale: int,
        band_count: int = 1,
        dtype: str = "FLOAT64",
        image_count: int = 1,
        forced: str = "auto",
        crs: str = "EPSG:3857",
    ) -> PlanResult:
        """决策入口。forced: auto / direct / export。

        P0-2：估算按目标 CRS 下的实际网格（Web Mercator 纬度拉伸 ~1/cos(φ)），
        不再用 geodesic 球面面积；P0-1：按 float64（8B/px）计字节，与 GEE
        getDownloadURL 的实际请求大小一致。
        """
        if forced == STRATEGY_DIRECT:
            return PlanResult(STRATEGY_DIRECT, "用户指定直接下载")
        if forced == STRATEGY_EXPORT:
            return PlanResult(STRATEGY_EXPORT, "用户指定 Export Task")

        est = estimate_raster_size_grid(region, scale, band_count, dtype, crs)

        # 多时相：超过阈值影像数强制 Export（避免创建大量直接下载）
        if image_count > self.config.export_force_threshold:
            return PlanResult(
                STRATEGY_EXPORT,
                f"{image_count} 个时间片，超过直接下载安全阈值，使用 Export Task",
                est,
                task_count=image_count,
            )

        # 单景尺寸判断
        if est.mb_total > self.config.direct_download_max_mb:
            return PlanResult(
                STRATEGY_EXPORT,
                f"估算 {est.mb_total:.1f} MB > 直接下载上限 "
                f"{self.config.direct_download_max_mb} MB，使用 Export Task",
                est,
            )
        if est.grid_dimension > self.config.max_grid_dimension:
            return PlanResult(
                STRATEGY_EXPORT,
                f"网格维度 {est.grid_dimension} > 上限 {self.config.max_grid_dimension}，使用 Export Task",
                est,
            )
        return PlanResult(
            STRATEGY_DIRECT,
            f"估算 {est.mb_total:.1f} MB / 网格 {est.grid_dimension}，在安全阈值内，直接下载",
            est,
        )

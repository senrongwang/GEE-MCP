"""下载请求模型（设计文档第 8.4 节 gee_download 输入）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from utils.dates import DateRangeError, parse_scale, validate_date_range

# 合法时间模式（设计文档第 12 节）
TIME_MODES = ("native", "daily", "monthly", "annual")
# 合法聚合方式（设计文档第 12 节）
AGGREGATIONS = ("mean", "median", "mosaic", "first", "best", "min", "max", "sum")
# 合法输出格式
FORMATS = ("GeoTIFF", "TFRecord", "Numpy")
# 合法下载策略
STRATEGIES = ("auto", "direct", "export")
# 输出 dtype（P2-10）：auto=保留 GEE 原始 float64；其余为 rasterio dtype 名
OUTPUT_DTYPES = ("auto", "float32", "float64", "int8", "uint8", "int16", "uint16",
                 "int32", "uint32")
# dtype 别名 -> 规范化名
_DTYPE_ALIASES = {
    "auto": "auto",
    "float": "float32", "single": "float32", "f4": "float32", "float32": "float32",
    "double": "float64", "f8": "float64", "float64": "float64",
    "byte": "uint8", "u1": "uint8", "uint8": "uint8",
    "int8": "int8", "i1": "int8",
    "short": "int16", "i2": "int16", "int16": "int16",
    "ushort": "uint16", "u2": "uint16", "uint16": "uint16",
    "int": "int32", "i4": "int32", "int32": "int32",
    "u4": "uint32", "uint32": "uint32",
}


def normalize_dtype(raw: Optional[str]) -> str:
    """规范化 dtype（别名转标准名），非法值返回 "auto" 并提示。"""
    key = (raw or "auto").strip().lower()
    return _DTYPE_ALIASES.get(key, "auto")


class RequestValidationError(ValueError):
    """下载请求参数校验失败。"""


@dataclass
class DownloadRequest:
    dataset: str
    start_date: str
    end_date: str
    boundary: str
    scale: str | int = 1000
    crs: str = "EPSG:3857"
    output: str = ""
    format: str = "GeoTIFF"
    # 时间模式：native=逐景 / daily / monthly / annual
    time_mode: str = "native"
    # 聚合方式（对每个时间片内的影像聚合）
    aggregation: Optional[str] = None
    # 是否裁剪到 boundary 像元（默认 False，只作 region 约束，见设计文档第 13 节）
    clip: bool = False
    # 只下载指定波段（如 ["EVI"]）；不填则下载全部波段
    bands: Optional[list[str]] = None
    # 时间维堆叠：把多个时间片合并为一个多波段 GeoTIFF（波段数=时间片数，每波段=一个时间片）
    # 默认 False：每个时间片单独输出一个文件（如 ndvi01.tif、ndvi02.tif）
    stack_periods: bool = False
    # 下载策略：auto=由 Download Planner 决定
    strategy: str = "auto"
    # 输出 dtype（P2-10）：auto=保留 GEE 原始 float64；
    # 指定 float32/int16/... 时下载后转换（+deflate 压缩），可大幅减小体积
    dtype: str = "auto"
    dry_run: bool = False
    description: str = ""
    # 网格对齐（设计文档第 16 节）：aligned=把 GEE 返回的分块重采样到
    # 期望对齐网格后再拼接（P1-4，默认开启，避免分块网格与请求矩形不一致导致拼接错位）
    grid_mode: str = "aligned"
    crs_transform: Optional[list] = None

    date_start: date = field(default=None, init=False, repr=False)  # type: ignore[assignment]
    date_end: date = field(default=None, init=False, repr=False)  # type: ignore[assignment]
    scale_m: int = field(default=None, init=False, repr=False)  # type: ignore[assignment]

    def validate(self) -> "DownloadRequest":
        """校验并规范化参数。"""
        if not self.dataset or not str(self.dataset).strip():
            raise RequestValidationError("dataset 不能为空")
        if not self.boundary or not str(self.boundary).strip():
            raise RequestValidationError("boundary（Boundary Asset ID）不能为空")

        try:
            self.date_start, self.date_end = validate_date_range(self.start_date, self.end_date)
        except DateRangeError as exc:
            raise RequestValidationError(str(exc)) from exc
        self.start_date = self.date_start.isoformat()
        self.end_date = self.date_end.isoformat()

        self.scale_m = parse_scale(self.scale)
        self.scale = self.scale_m

        crs = str(self.crs or "EPSG:3857").strip()
        if not crs.upper().startswith("EPSG:"):
            raise RequestValidationError(f"crs 必须是 EPSG:xxxx 格式，收到: {crs!r}")
        self.crs = crs.upper()

        if self.format not in FORMATS:
            raise RequestValidationError(
                f"format 必须是 {FORMATS} 之一，收到: {self.format!r}"
            )

        mode = (self.time_mode or "native").lower()
        if mode not in TIME_MODES:
            raise RequestValidationError(
                f"time_mode 必须是 {TIME_MODES} 之一，收到: {self.time_mode!r}"
            )
        self.time_mode = mode

        if self.aggregation:
            agg = self.aggregation.lower()
            if agg not in AGGREGATIONS:
                raise RequestValidationError(
                    f"aggregation 必须是 {AGGREGATIONS} 之一，收到: {self.aggregation!r}"
                )
            self.aggregation = agg

        if self.bands:
            cleaned = [str(b).strip() for b in self.bands if str(b).strip()]
            self.bands = cleaned or None

        strategy = (self.strategy or "auto").lower()
        if strategy not in STRATEGIES:
            raise RequestValidationError(
                f"strategy 必须是 {STRATEGIES} 之一，收到: {self.strategy!r}"
            )
        self.strategy = strategy

        # P2-10：输出 dtype（别名归一化，非法值报错）
        norm_dtype = normalize_dtype(self.dtype)
        if norm_dtype == "auto" and str(self.dtype or "auto").strip().lower() != "auto":
            raise RequestValidationError(
                f"dtype 必须是 {OUTPUT_DTYPES} 之一，收到: {self.dtype!r}"
            )
        self.dtype = norm_dtype

        if not self.output.strip():
            raise RequestValidationError("output（输出目录）不能为空")
        if self.grid_mode not in ("native", "target_crs", "aligned"):
            raise RequestValidationError(
                f"grid_mode 必须是 native/target_crs/aligned，收到: {self.grid_mode!r}"
            )

        if not self.description:
            self.description = (
                f"{self.dataset.replace('/', '_')}_{self.start_date}_{self.end_date}"
            )
        return self

    def to_plain(self) -> dict:
        """返回给 AI 的纯 JSON 参数字典（不含内部字段）。"""
        return {
            "dataset": self.dataset,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "boundary": self.boundary,
            "scale": self.scale_m,
            "crs": self.crs,
            "output": self.output,
            "format": self.format,
            "time_mode": self.time_mode,
            "aggregation": self.aggregation,
            "clip": self.clip,
            "bands": self.bands,
            "stack_periods": self.stack_periods,
            "strategy": self.strategy,
            "dtype": self.dtype,
            "dry_run": self.dry_run,
            "grid_mode": self.grid_mode,
        }

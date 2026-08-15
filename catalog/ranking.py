"""Ranking：候选数据集的规则打分与 Match Reasons。

对应设计文档《GEE Dataset Discovery》第 10、16 节。

Score = keyword + band + resolution + temporal + date_coverage + platform + region
（加权平均归一化到 0..1）
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from models.dataset import DatasetRecord

# 各分量权重（可调）
WEIGHTS = {
    "keyword": 2.0,
    "band": 3.0,
    "resolution": 1.0,
    "temporal": 1.0,
    "date": 1.5,
    "platform": 0.5,
    "region": 0.5,
}

# 时间分辨率阶梯（daily 最贴合“日尺度”需求）
_TEMPORAL_LADDER = ["daily", "8-day", "16-day", "monthly", "annual"]


# ---------------- 各分量打分 ----------------
def keyword_score(record: DatasetRecord, terms: list[str]) -> float:
    """关键词匹配优先级：Band 名 == 词 > 数据集名包含 > 描述包含 > 标签包含。"""
    if not terms:
        return 0.0
    name_l = record.name.lower()
    desc_l = record.description.lower()
    tags_l = {t.lower() for t in record.tags}
    band_l = {b.name.lower() for b in record.bands}
    best = 0.0
    for t in terms:
        t = t.lower()
        if not t:
            continue
        if t in band_l:
            best = max(best, 1.0)
        elif t in name_l:
            best = max(best, 0.8)
        elif t in desc_l:
            best = max(best, 0.6)
        elif any(t in tag for tag in tags_l):
            best = max(best, 0.4)
        else:
            # 部分匹配（如 "vegetation" 匹配 "vegetation-indices"）
            if any(t in tag for tag in tags_l):
                best = max(best, 0.3)
    return best


def band_score(record: DatasetRecord, requested_bands: Optional[list[str]]) -> float:
    """明确要求的 Band 命中率（命中全部=1，部分=比例）。"""
    if not requested_bands:
        return 0.0
    band_l = {b.name.lower() for b in record.bands}
    hit = sum(1 for b in requested_bands if b.lower() in band_l)
    return hit / len(requested_bands)


def resolution_score(actual: Optional[float], preferred: Optional[float],
                     tolerance: float = 1.0) -> float:
    """空间分辨率匹配度：1 / (1 + abs(log2(actual / preferred)) / tolerance)。

    设计文档第 10 节公式（tolerance=1 时为原始公式）：
        1000m 要求下：1000m→1.0，500m→0.5，2000m→0.5，250m→0.33，5000m→0.30
    """
    if preferred is None or actual is None or actual <= 0:
        return 0.0
    ratio = actual / preferred
    try:
        return 1.0 / (1.0 + abs(math.log2(ratio)) / max(tolerance, 1e-9))
    except (ValueError, OverflowError):
        return 0.0


def temporal_score(actual: Optional[str], preferred: Optional[str]) -> float:
    """时间分辨率阶梯分：daily > 8-day > 16-day > monthly > annual。"""
    if preferred is None or actual is None:
        return 0.0
    if preferred not in _TEMPORAL_LADDER or actual not in _TEMPORAL_LADDER:
        return 0.0
    pref_idx = _TEMPORAL_LADDER.index(preferred)
    act_idx = _TEMPORAL_LADDER.index(actual)
    # 完全一致 = 1；更细（如要求 monthly 但数据是 daily）= 0.9；
    # 每粗一档递减 0.2，最低 0.2
    if act_idx == pref_idx:
        return 1.0
    if act_idx < pref_idx:
        return 0.9
    return max(0.2, round(1.0 - 0.2 * (act_idx - pref_idx), 2))


def date_coverage_score(record: DatasetRecord,
                        req_start: Optional[date],
                        req_end: Optional[date]) -> tuple[float, str]:
    """日期覆盖：完整=1，部分=0.5，无重叠=0（状态 FULL/PARTIAL/NONE）。

    数据集 end_date 为空（开放结尾 / NRT 数据集）时按“至今”处理。
    """
    if req_start is None and req_end is None:
        return 1.0, "FULL"
    if not record.start_date and not record.end_date:
        return 0.5, "UNKNOWN"

    def _d(s):
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None

    r_start, r_end = _d(record.start_date), _d(record.end_date)
    if r_end is None:
        r_end = date.today()  # 开放结尾按至今覆盖
    if r_start is None or r_end is None:
        return 0.5, "UNKNOWN"
    if req_start and req_end:
        if r_start <= req_start and r_end >= req_end:
            return 1.0, "FULL"
        if r_end < req_start or r_start > req_end:
            return 0.0, "NONE"
        return 0.5, "PARTIAL"
    if req_start is not None:
        return (1.0, "FULL") if r_end >= req_start else (0.0, "NONE")
    if req_end is not None:
        return (1.0, "FULL") if r_start <= req_end else (0.0, "NONE")
    return 1.0, "FULL"


def platform_score(record: DatasetRecord, platforms: Optional[list[str]]) -> float:
    if not platforms:
        return 0.0
    p_l = record.platform.lower()
    return 1.0 if any(p.lower() in p_l for p in platforms) else 0.0


_REGION_BBOX = {
    "China": (73.0, 18.0, 135.0, 54.0),
    "Asia": (26.0, -11.0, 180.0, 77.0),
    "Europe": (-25.0, 34.0, 45.0, 72.0),
    "North America": (-170.0, 7.0, -52.0, 84.0),
}


def _bbox_intersects(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def region_score(record: DatasetRecord, region: Optional[str]) -> float:
    """区域匹配（MVP 用 bbox 相交 + global 覆盖判断，启发式）。"""
    if region is None or region.lower() == "global":
        return 0.0  # 不构成加分项，也不减分
    if record.coverage and "global" in record.coverage.lower():
        return 1.0
    if record.bbox and len(record.bbox) == 4:
        rb = _REGION_BBOX.get(region)
        if rb and _bbox_intersects(tuple(record.bbox), rb):
            return 1.0
    return 0.0


# ---------------- 总评分 ----------------
def total_score(
    record: DatasetRecord,
    *,
    terms: Optional[list[str]] = None,
    requested_bands: Optional[list[str]] = None,
    preferred_resolution: Optional[float] = None,
    resolution_tolerance: float = 2.0,
    preferred_temporal: Optional[str] = None,
    req_start: Optional[date] = None,
    req_end: Optional[date] = None,
    platforms: Optional[list[str]] = None,
    region: Optional[str] = None,
) -> float:
    """加权平均总分（0..1）。"""
    parts = {
        "keyword": keyword_score(record, terms or []),
        "band": band_score(record, requested_bands),
        "resolution": resolution_score(record.spatial_resolution,
                                       preferred_resolution, resolution_tolerance),
        "temporal": temporal_score(record.temporal_resolution, preferred_temporal),
        "date": date_coverage_score(record, req_start, req_end)[0],
        "platform": platform_score(record, platforms),
        "region": region_score(record, region),
    }
    weights = WEIGHTS
    # 未提供对应条件的分量不计入分母
    active = []
    for key, value in parts.items():
        if _component_used(key, terms, requested_bands, preferred_resolution,
                           preferred_temporal, req_start, req_end, platforms, region):
            active.append((key, value))
    if not active:
        return 0.0
    total = sum(weights[k] * v for k, v in active)
    return total / sum(weights[k] for k, _ in active)


def _component_used(key, terms, requested_bands, preferred_resolution,
                    preferred_temporal, req_start, req_end, platforms, region) -> bool:
    if key == "keyword":
        return bool(terms)
    if key == "band":
        return bool(requested_bands)
    if key == "resolution":
        return preferred_resolution is not None
    if key == "temporal":
        return preferred_temporal is not None
    if key == "date":
        return req_start is not None or req_end is not None
    if key == "platform":
        return bool(platforms)
    if key == "region":
        return region is not None and region.lower() != "global"
    return False


# ---------------- Match Reasons ----------------
def build_match_reasons(
    record: DatasetRecord,
    *,
    terms: Optional[list[str]] = None,
    requested_bands: Optional[list[str]] = None,
    preferred_resolution: Optional[float] = None,
    preferred_temporal: Optional[str] = None,
    req_start: Optional[date] = None,
    req_end: Optional[date] = None,
    platforms: Optional[list[str]] = None,
    region: Optional[str] = None,
) -> list[str]:
    """生成人类可读的匹配理由（设计文档第 16 节）。"""
    reasons: list[str] = []

    if terms:
        hit = _keyword_hit(record, terms)
        if hit:
            reasons.append(hit)

    if requested_bands:
        band_l = {b.name.lower() for b in record.bands}
        missing = [b for b in requested_bands if b.lower() not in band_l]
        hit_names = [b for b in requested_bands if b.lower() in band_l]
        if not missing:
            reasons.append(f"{', '.join(hit_names)} band available")
        else:
            reasons.append(f"缺少 band: {', '.join(missing)}")

    if preferred_resolution is not None and record.spatial_resolution:
        s = resolution_score(record.spatial_resolution, preferred_resolution)
        reasons.append(
            f"空间分辨率 {record.spatial_resolution:g} m 接近 {preferred_resolution:g} m"
            f"（匹配度 {s:.0%}）"
        )

    if preferred_temporal and record.temporal_resolution:
        s = temporal_score(record.temporal_resolution, preferred_temporal)
        if s >= 0.8:
            reasons.append(f"{record.temporal_resolution} 时间分辨率")
        else:
            reasons.append(f"{record.temporal_resolution} 时间分辨率（非首选 {preferred_temporal}）")

    if req_start or req_end:
        score, status = date_coverage_score(record, req_start, req_end)
        if status == "FULL":
            reasons.append("完整覆盖请求时间段")
        elif status == "PARTIAL":
            reasons.append("PARTIAL COVERAGE：仅部分覆盖请求时间段")
        elif status == "NONE":
            reasons.append("不覆盖请求时间段")

    if platforms and platform_score(record, platforms) == 1.0:
        reasons.append(f"平台匹配: {record.platform}")

    if region and region.lower() != "global":
        if region_score(record, region) == 1.0:
            reasons.append(f"覆盖 {region}")
        else:
            reasons.append(f"可能不覆盖 {region}（Catalog bbox 未相交）")

    if record.coverage and "global" in record.coverage.lower():
        reasons.append("Global coverage")

    return reasons


def _keyword_hit(record: DatasetRecord, terms: list[str]) -> Optional[str]:
    name_l = record.name.lower()
    desc_l = record.description.lower()
    band_l = {b.name.lower() for b in record.bands}
    tags_l = {t.lower() for t in record.tags}
    for t in terms:
        t = t.lower()
        if not t:
            continue
        if t in band_l:
            return f"Band '{t}' 精确匹配"
        if t in name_l:
            return f"名称包含 '{t}'"
        if t in desc_l:
            return f"描述包含 '{t}'"
        if any(t in tag for tag in tags_l):
            return f"标签包含 '{t}'"
    return None

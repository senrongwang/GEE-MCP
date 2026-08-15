"""AI GEE Downloader —— 本地 MCP Server 入口（设计文档第 5、8 节）。

工具：
  gee_login            登录 GEE / 检查认证状态 / 初始化
  gee_dataset_info     获取数据集信息（Image / ImageCollection / FeatureCollection）
  gee_boundary_info    检查 Boundary Asset
  gee_download         核心下载工具（dry_run 支持）
  gee_task_status      查询任务状态
  gee_list_tasks       列出当前用户的 Export Tasks / 本地任务

运行：
  python server.py            （stdio 传输，供 Claude Desktop / Cursor / 其他 MCP 客户端注册）
"""

from __future__ import annotations

import sys
from typing import Optional

from mcp.server.mcpserver import MCPServer

from config import Config
from download.manager import DownloadManager
from gee.auth import GeeAuthError, login as gee_login_impl
from gee.boundary import BoundaryError, BoundaryResolver
from gee.dataset import DatasetNotFoundError, DatasetResolver
from gee.task import describe_task, list_export_tasks
from models.request import DownloadRequest, RequestValidationError
from utils.logging import get_logger, setup_logging

logger = get_logger(__name__)

mcp = MCPServer(
    name="AI GEE Downloader",
    instructions=(
        "AI 原生的 Google Earth Engine（GEE）遥感数据下载工具："
        "系统自动判断 Image/ImageCollection、筛选时间、解析边界、规划下载策略"
        "（默认本地直下，必要时自动分片拼接，不经 Drive）、执行下载、检查 GeoTIFF、生成元数据。\n"
        "【gee_download 必选参数】dataset（数据集 ID）、start_date（YYYY-MM-DD）、"
        "end_date（YYYY-MM-DD）、boundary（Boundary Asset ID）。\n"
        "【可选参数】output（默认 D:/GEE_Data，建议显式指定）、scale（默认 1000m，支持 '1km'/'250m'）、"
        "crs（默认 EPSG:3857）、bands（如 [\"EVI\"]，默认全部）、time_mode（native/daily/monthly/annual）、"
        "aggregation（mean/median/mosaic/first/best/min/max/sum）、dry_run（大任务必须先用 true 预览）、"
        "strategy（auto=本地直下）、clip、description。\n"
        "【调用流程】1) 首次先 gee_login；2) 用 gee_dataset_info 确认数据集与波段；"
        "3) 用 gee_boundary_info 确认边界；4) 大规模下载先 gee_download(dry_run=true) 征询用户；"
        "5) 提交后用 gee_task_status 轮询，gee_list_tasks 查看历史。\n"
        "【提醒】参数不确定时先调用 gee_help 查看完整说明。"
    ),
)

# gee_download 必选参数清单（用于校验错误时的提醒）
_DOWNLOAD_REQUIRED_PARAMS = {
    "dataset": "GEE 数据集 ID，如 MODIS/061/MOD13A2",
    "start_date": "开始日期，YYYY-MM-DD",
    "end_date": "结束日期，YYYY-MM-DD",
    "boundary": "Boundary Asset ID，如 projects/xxx/assets/CUS",
}

# 全局单例
_config: Optional[Config] = None
_manager: Optional[DownloadManager] = None


def _get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def _get_manager() -> DownloadManager:
    global _manager
    if _manager is None:
        _manager = DownloadManager(_get_config())
    return _manager


# ---------------------------------------------------------------- 工具
@mcp.tool()
def gee_login(force: bool = False) -> dict:
    """登录 Google Earth Engine：检查本地凭据 -> 无凭据则打开 OAuth 浏览器流程 -> 初始化。

    Args:
        force: 强制重新 OAuth 登录（即使已有凭据）
    """
    try:
        return gee_login_impl(force=force, config=_get_config())
    except GeeAuthError as exc:
        return _err("GEE authentication failed", "认证错误", str(exc),
                    "1. 确认浏览器能打开 Google 登录页。\n2. 检查网络 / 代理设置。\n3. 重试 gee_login(force=true)。")
    except Exception as exc:  # noqa: BLE001
        return _err("login failed", "登录失败", str(exc), "查看日志后重试。")


@mcp.tool()
def gee_dataset_info(dataset_id: str) -> dict:
    """获取 GEE 数据集信息：类型（Image / ImageCollection）、波段、CRS、分辨率、时间范围。

    Args:
        dataset_id: GEE 数据集 ID，如 MODIS/061/MOD13Q1、LANDSAT/LC09/C02/T1_L2
    """
    try:
        from gee.auth import ensure_initialized
        ensure_initialized(_get_config())
        info = DatasetResolver().inspect(dataset_id)
        return {"status": "ok", **info.to_dict()}
    except GeeAuthError as exc:
        return _auth_err(exc)
    except DatasetNotFoundError as exc:
        return _err("Dataset not found", "数据集不存在", str(exc),
                    "1. 检查数据集 ID 拼写。\n2. 到 GEE Data Catalog 确认 ID。\n3. 确认当前账号有权限访问。")
    except Exception as exc:  # noqa: BLE001
        return _err("dataset info failed", "获取数据集信息失败", str(exc), "确认已登录（gee_login）。")


@mcp.tool()
def gee_boundary_info(asset_id: str) -> dict:
    """检查 Boundary Asset：要素数量、范围、面积。

    Args:
        asset_id: 边界资产 ID，如 projects/xxx/assets/Anhui 或 users/xxx/Anhui
    """
    try:
        from gee.auth import ensure_initialized
        ensure_initialized(_get_config())
        _, info = BoundaryResolver().resolve(asset_id)
        return {"status": "ok", **info.to_dict()}
    except GeeAuthError as exc:
        return _auth_err(exc)
    except BoundaryError as exc:
        return _err("Boundary Asset not found", "边界资产无法访问", str(exc),
                    "1. 检查 Asset 是否属于当前账号。\n2. 检查 Asset sharing 权限。\n3. 用正确的 projects/ 或 users/ 前缀。")
    except Exception as exc:  # noqa: BLE001
        return _err("boundary info failed", "获取边界信息失败", str(exc), "确认已登录（gee_login）。")


@mcp.tool()
def gee_help(topic: Optional[str] = None) -> dict:
    """查看本 MCP 的完整使用说明：参数清单（必选/可选）、调用流程、示例与常见错误。

    当用户或 AI 不确定要提供哪些参数、或工具返回参数错误时，调用本工具获取引导。

    Args:
        topic: 可选，指定查看的主题：download / login / dataset_info / boundary_info /
               task_status / list_tasks / workflow / examples；不填返回总览。
    """
    return {"status": "ok", **_HELP_DOC(topic)}


def _HELP_DOC(topic: Optional[str] = None) -> dict:
    """生成帮助文档（结构化，便于 AI 直接引用）。"""
    topic = (topic or "").strip().lower()

    overview = {
        "工具清单": {
            "gee_login": "登录 GEE / 检查认证状态 / 初始化（参数：force 可选）",
            "gee_dataset_info": "获取数据集信息（参数：dataset_id 必选）",
            "gee_boundary_info": "检查 Boundary Asset（参数：asset_id 必选）",
            "gee_download": "核心下载（4 个必选参数 + 11 个可选参数）",
            "gee_task_status": "查询任务状态（参数：task_id 必选）",
            "gee_list_tasks": "列出本地任务 / GEE 任务（参数：state、source 可选）",
        },
        "gee_download 必选参数": {
            "dataset": "GEE 数据集 ID，如 MODIS/061/MOD13A2",
            "start_date": "开始日期，YYYY-MM-DD",
            "end_date": "结束日期，YYYY-MM-DD",
            "boundary": "Boundary Asset ID，如 projects/xxx/assets/CUS",
        },
        "gee_download 可选参数": {
            "output": "输出目录（默认 D:/GEE_Data，建议显式指定）",
            "scale": "分辨率（默认 1000m；支持 '250m'/'1km'/'9000'）",
            "crs": "坐标系（默认 EPSG:3857）",
            "bands": "只下载指定波段，如 [\"EVI\"]（默认全部）",
            "time_mode": "native(逐景)/daily/monthly/annual（默认 native）",
            "aggregation": "mean/median/mosaic/first/best/min/max/sum（monthly/annual 默认 mean）",
            "clip": "是否裁剪到边界像元（默认 False，仅作 region 约束）",
            "strategy": "auto=本地直下（默认）/ direct / export",
            "dry_run": "只规划不下载（默认 False；大规模数据必须先用 true）",
            "format": "输出格式（默认 GeoTIFF）",
            "description": "任务描述（默认自动生成）",
        },
        "调用流程": [
            "1. 首次使用先调用 gee_login（浏览器 OAuth，凭据持久化）",
            "2. gee_dataset_info 确认数据集类型（Image / ImageCollection）与波段",
            "3. gee_boundary_info 确认边界资产可访问",
            "4. 大规模下载先用 gee_download(dry_run=true) 预览：影像数 / 估算体积 / 策略 / 任务数，征询用户后再执行",
            "5. 提交后 gee_download 立即返回 task_id，用 gee_task_status 轮询",
            "6. 完成后返回本地文件路径、QA 报告与 metadata.json",
        ],
        "示例": {
            "MODIS EVI 1km 单天": {
                "dataset": "MODIS/061/MOD13A2",
                "start_date": "2021-01-01",
                "end_date": "2021-01-01",
                "boundary": "projects/xxx/assets/CUS",
                "scale": "1km",
                "bands": ["EVI"],
                "output": "D:/GEE_Data",
            },
            "月平均多时相": {
                "dataset": "MODIS/061/MOD13Q1",
                "start_date": "2021-01-01",
                "end_date": "2021-12-31",
                "boundary": "projects/xxx/assets/Anhui",
                "scale": "250m",
                "time_mode": "monthly",
                "aggregation": "mean",
                "output": "D:/GEE_Data",
            },
        },
    }

    if not topic or topic == "overview":
        return overview

    if topic == "download":
        return {k: v for k, v in overview.items() if k != "工具清单"}
    if topic in ("login", "dataset_info", "boundary_info", "task_status", "list_tasks"):
        return {
            "工具": overview["工具清单"][f"gee_{topic}"],
            "提醒": "参数缺失或错误时，工具会返回『问题/原因/建议』结构，按建议处理即可。",
        }
    if topic == "workflow":
        return {"调用流程": overview["调用流程"]}
    if topic == "examples":
        return {"示例": overview["示例"]}
    return {**overview, "警告": f"未知主题 {topic!r}，已返回总览"}


@mcp.tool()
def gee_download(
    dataset: str,
    start_date: str,
    end_date: str,
    boundary: str,
    scale: str | int = 1000,
    crs: str = "EPSG:3857",
    output: str = "",
    format: str = "GeoTIFF",
    time_mode: str = "native",
    aggregation: Optional[str] = None,
    clip: bool = False,
    bands: Optional[list[str]] = None,
    strategy: str = "auto",
    dry_run: bool = False,
    description: str = "",
) -> dict:
    """核心下载工具：解析参数 -> 识别数据集 -> 时间筛选 -> 边界解析 -> 规划 -> 下载 -> QA -> 元数据。

    Args:
        dataset: GEE 数据集 ID（Image 或 ImageCollection）
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        boundary: Boundary Asset ID（projects/xxx/assets/xxx 或 users/xxx/xxx）
        scale: 分辨率（米），支持 250m / 1km / 9000 等写法
        crs: 输出坐标系，默认 EPSG:3857
        output: 本地输出目录（必须在配置白名单内）
        format: 输出格式，默认 GeoTIFF
        time_mode: native（逐景）/ daily / monthly / annual
        aggregation: 每个时间片内的聚合：mean/median/mosaic/first/best/min/max/sum
        clip: 是否裁剪到边界像元（默认 False，仅作 region 约束）
        bands: 只下载指定波段，如 ["EVI"]；不填则全部波段
        strategy: auto（自动选择）/ direct / export
        dry_run: 只做规划并返回估算，不执行下载（大数据量必须先用 dry_run）
        description: 任务描述（默认自动生成）

    Returns:
        非 dry_run 时立即返回 task_id，AI 用 gee_task_status 轮询。
    """
    req = DownloadRequest(
        dataset=dataset,
        start_date=start_date,
        end_date=end_date,
        boundary=boundary,
        scale=scale,
        crs=crs,
        output=output or _get_config().default_output,
        format=format,
        time_mode=time_mode,
        aggregation=aggregation,
        clip=clip,
        bands=bands,
        strategy=strategy,
        dry_run=dry_run,
        description=description,
    )
    try:
        req.validate()
    except RequestValidationError as exc:
        advice = (
            "gee_download 必选参数：dataset / start_date / end_date / boundary；"
            "常用可选参数：output / scale / crs / bands / dry_run。\n"
            f"必选参数说明：{_fmt_required()}。\n"
            "可调用 gee_help(topic='download') 查看完整说明。"
        )
        return _err("Invalid request", "参数错误", str(exc), advice)

    if req.dry_run:
        try:
            plan = _get_manager().plan(req)
            return {"status": "ok", "dry_run": True, "plan": plan}
        except GeeAuthError as exc:
            return _auth_err(exc)
        except Exception as exc:  # noqa: BLE001
            return _err("dry_run failed", "规划失败", str(exc),
                        "1. 确认已登录（gee_login）。\n2. 用 gee_dataset_info / gee_boundary_info 检查参数。")

    try:
        record = _get_manager().submit(req)
        return {
            "status": "ok",
            "task_id": record.task_id,
            "state": record.state,
            "description": record.description,
            "message": "任务已提交。使用 gee_task_status(task_id=...) 查询进度。",
        }
    except GeeAuthError as exc:
        return _auth_err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("submit failed", "任务提交失败", str(exc),
                    "1. 确认已登录。\n2. 先用 dry_run=true 验证参数。")


@mcp.tool()
def gee_task_status(task_id: str) -> dict:
    """查询本地下载任务状态（gee_task_status）。

    Args:
        task_id: gee_download 返回的本地 task_id
    """
    try:
        status = _get_manager().status(task_id)
        if status is None:
            return _err("Task not found", "任务不存在", f"task_id={task_id}",
                        "检查 task_id 是否正确，或用 gee_list_tasks 查看所有任务。")
        return {"status": "ok", **status}
    except Exception as exc:  # noqa: BLE001
        return _err("task status failed", "查询任务状态失败", str(exc), "查看日志后重试。")


@mcp.tool()
def gee_list_tasks(state: Optional[str] = None, source: str = "local") -> dict:
    """列出任务（gee_list_tasks）。

    Args:
        state: 按状态过滤（PENDING/PLANNING/RUNNING/COMPLETED/FAILED/...），不填则全部
        source: local（本地任务库，推荐）或 gee（当前账号的 GEE Export Tasks）
    """
    try:
        if source == "gee":
            from gee.auth import ensure_initialized
            ensure_initialized(_get_config())
            tasks = list_export_tasks(status_filter=state)
            return {"status": "ok", "source": "gee", "tasks": tasks}
        tasks = _get_manager().list_tasks(state=state)
        return {"status": "ok", "source": "local", "tasks": tasks}
    except GeeAuthError as exc:
        return _auth_err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("list tasks failed", "列出任务失败", str(exc),
                    "确认已登录（gee_login）。")


# ---------------------------------------------------------------- 错误格式化（设计文档第 31 节）
def _fmt_required() -> str:
    return "；".join(f"{k}（{v}）" for k, v in _DOWNLOAD_REQUIRED_PARAMS.items())


def _auth_err(exc: Exception) -> dict:
    return _err(
        "GEE authentication failed",
        "认证错误：需要先登录 GEE",
        str(exc),
        "1. 调用 gee_login 完成 OAuth 登录（浏览器会打开 Google 登录页）。\n"
        "2. 若网络受限，确认代理可用（GEE 需要能访问 Google）。\n"
        "3. 登录成功后重新调用本工具。",
    )


def _err(code: str, problem: str, reason: str, advice: str) -> dict:
    """结构化错误：问题 / 原因 / 建议。"""
    return {
        "status": "error",
        "error": {
            "code": code,
            "问题": problem,
            "原因": reason,
            "建议": advice,
        },
    }


def main() -> None:
    setup_logging(_get_config().data.get("logging", {}).get("level", "INFO"),
                  _get_config().data.get("logging", {}).get("redact_secrets", True))
    logger.info("AI GEE Downloader MCP Server 启动（stdio）")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

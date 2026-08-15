"""下载管理器：编排整个下载流程（设计文档第 28、29、33 节）。

状态机：PENDING -> VALIDATING -> PLANNING -> SUBMITTING -> RUNNING
        -> DOWNLOADING -> VALIDATING_OUTPUT -> COMPLETED
失败：任何状态 -> FAILED；取消：RUNNING -> CANCELLED

gee_download 提交后立即返回 task_id，管线在后台线程执行，
AI 通过 gee_task_status / gee_list_tasks 轮询（设计文档第 8.5、8.6 节）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import ee

from config import Config
from download.direct import (
    DirectDownloadError,
    direct_download_image,
    stack_period_files,
)
from download.drive import DriveDownloader, DriveDownloadError, drive_web_url
from download.export import run_export_to_drive
from gee.auth import ensure_initialized
from gee.boundary import BoundaryResolver
from gee.collection import aggregate_per_period, build_collection, list_images
from gee.dataset import DatasetResolver, DatasetType
from gee.export import ExportSpec
from models.request import DownloadRequest
from models.result import DownloadResult
from models.task import TaskRecord, TaskStore
from planner.download_planner import DownloadPlanner, STRATEGY_DIRECT, STRATEGY_EXPORT
from planner.size_estimator import estimate_raster_size
from planner.temporal_planner import TemporalPlanner
from raster.metadata import write_metadata
from raster.validate import validate_file
from utils.dates import iter_periods
from utils.logging import get_logger
from utils.paths import make_output_dir, safe_filename

logger = get_logger(__name__)

# 原生逐景导出的任务数上限，超过则建议分组
_MAX_NATIVE_TASKS = 30


class DownloadError(RuntimeError):
    """下载流程失败。"""


class DownloadManager:
    """本地下载管理器：AI 只给结构化参数，所有 GEE 业务逻辑在此执行。"""

    def __init__(self, config: Optional[Config] = None, store: Optional[TaskStore] = None):
        self.config = config or Config.load()
        default_store_root = Path(self.config.default_output) / "metadata" / "tasks"
        self.store = store or TaskStore(default_store_root)
        self.planner = DownloadPlanner(self.config)
        self._threads: dict[str, threading.Thread] = {}

    # ================= 规划（dry_run 用） =================
    def plan(self, request: DownloadRequest) -> dict:
        """执行 VALIDATING + PLANNING，返回规划结果（设计文档第 21 节 dry_run 格式）。"""
        session = ensure_initialized(self.config)
        request.validate()

        resolver = DatasetResolver()
        dsinfo = resolver.inspect(request.dataset)
        bres = BoundaryResolver()
        fc, binfo = bres.resolve(request.boundary)
        region = fc.geometry()

        images = []
        coll = None
        fallback = False
        if dsinfo.type == DatasetType.IMAGE_COLLECTION:
            coll = build_collection(
                request.dataset, request.start_date, request.end_date, region)
            images = list_images(coll)
            if not images and request.start_date == request.end_date:
                # 单日请求 + 合成数据（如 MODIS 16 天）：回退取该日期前最近一景
                fallback = True
                count = 1
            else:
                count = len(images)
        elif dsinfo.type == DatasetType.IMAGE:
            count = 1
        else:
            raise DownloadError(
                f"数据集类型 {dsinfo.type} 不支持下载，仅支持 Image / ImageCollection"
            )

        temporal = TemporalPlanner().plan(
            images, request.date_start, request.date_end, request.aggregation)

        # 分片规划：估算外包矩形在目标网格下的直接下载分片数（本地优先策略依据）
        from download.direct import plan_chunks
        try:
            tile_count = len(plan_chunks(region, request.scale_m, request.crs))
        except Exception:  # noqa: BLE001
            tile_count = 1

        # 保护：分片数超过上限时报错（避免大量 getDownloadURL 请求触发 GEE 配额）。
        # 默认永不自动 Export；如确实需要远程中转，请显式 strategy="export"。
        max_tiles = self.config.max_direct_tiles
        if strategy_forced_export(request) is False and tile_count > max_tiles:
            raise DownloadError(
                f"该任务需 {tile_count} 个分片，超过本地直下保护上限 {max_tiles}，"
                "请缩小空间范围 / 提高分辨率，或显式指定 strategy=\"export\"（远程中转）。"
            )

        area = _region_area(region)
        band_count = max(1, len(request.bands) if request.bands
                         else (len(dsinfo.bands) or 1))
        est = estimate_raster_size(area, request.scale_m, band_count)

        strategy = self._decide_strategy(
            request, count, tile_count, est.mb_total, est.grid_dimension)
        mode = self._effective_time_mode(request, temporal.strategy, count)
        effective_agg = None
        if mode in ("monthly", "annual", "daily"):
            effective_agg = request.aggregation or "mean"

        plan = {
            "dataset": request.dataset,
            "type": dsinfo.type,
            "image_count": count,
            "region": "User Boundary",
            "boundary": request.boundary,
            "boundary_info": binfo.to_dict(),
            "resolution_m": request.scale_m,
            "crs": request.crs,
            "time_mode": mode,
            "aggregation": request.aggregation,
            "aggregation_effective": effective_agg,
            "fallback_nearest": fallback,
            "stack_periods": request.stack_periods,
            "estimated_mb": round(est.mb_total, 2),
            "estimated_gb": round(est.mb_total / 1024, 3),
            "estimated_pixels": est.pixel_count,
            "recommended_strategy": strategy,
            "estimated_tasks": self._estimate_tasks(temporal, request, mode),
            "tile_count": tile_count,
            "temporal_recommendation": temporal.recommendation,
            "bands": request.bands or dsinfo.bands,
            "band_count": len(request.bands) if request.bands
                          else (len(dsinfo.bands) or 1),
            "dataset_info": dsinfo.to_dict(),
        }
        if request.stack_periods:
            # 堆叠后：输出 1 个多波段 tif，波段数 = 时间片数 × 每片波段数
            periods = self._estimate_tasks(temporal, request, mode)
            plan["output_files"] = 1 if periods > 1 else periods
            plan["stacked_band_count"] = periods * (plan["band_count"] or 1)
        else:
            plan["output_files"] = self._estimate_tasks(temporal, request, mode)
        if dsinfo.time_start:
            plan["dataset_time_range"] = [dsinfo.time_start, dsinfo.time_end]
        return plan

    # ================= 提交任务 =================
    def submit(self, request: DownloadRequest) -> TaskRecord:
        """校验请求并后台执行下载管线，立即返回 task_id。"""
        request.validate()
        record = TaskRecord(
            description=request.description or f"{request.dataset} {request.start_date}..{request.end_date}",
            dataset=request.dataset,
            request=request.to_plain(),
        )
        self.store.save(record)

        t = threading.Thread(
            target=self._run_pipeline,
            args=(record, request),
            daemon=True,
            name=f"gee-task-{record.task_id}",
        )
        self._threads[record.task_id] = t
        t.start()
        return record

    # ================= 状态查询 =================
    def status(self, task_id: str) -> Optional[dict]:
        record = self.store.load(task_id)
        if record is None:
            return None
        d = record.to_dict()
        # 若底层 GEE Export 在跑，补充实时状态
        if record.gee_task_id and record.state not in ("COMPLETED", "FAILED", "CANCELLED"):
            try:
                import ee
                st = ee.data.getTaskStatus(record.gee_task_id)
                if st:
                    d["gee_state"] = st[0].get("state")
                    d["gee_error_message"] = st[0].get("error_message")
            except Exception:  # noqa: BLE001
                pass
        return d

    def list_tasks(self, state: Optional[str] = None) -> list[dict]:
        recs = self.store.list()
        out = []
        for r in recs:
            d = r.to_dict()
            if state and d["state"] != state.upper():
                continue
            out.append({
                "task_id": d["task_id"],
                "state": d["state"],
                "description": d["description"],
                "dataset": d["dataset"],
                "strategy": d["strategy"],
                "created_at": d["created_at"],
                "error": d.get("error"),
            })
        return out

    # ================= 内部：决策 =================
    def _decide_strategy(self, request, image_count, tile_count, est_mb, grid_dim) -> str:
        """本地优先策略（默认永不远程导出）：

        - 默认（auto / direct）-> 始终 Direct Download 本地直下（自动分片拼接），不经 Drive
        - 仅当用户显式指定 strategy="export" 时才使用 Export Task（远程中转，默认不用）
        """
        if request.strategy == "export":
            return STRATEGY_EXPORT
        return STRATEGY_DIRECT

    def _effective_time_mode(self, request, temporal_strategy, count) -> str:
        if request.time_mode != "native":
            return request.time_mode
        # 用户未指定分组：按时间规划器推荐
        if temporal_strategy == "annual":
            return "annual"
        if temporal_strategy == "monthly":
            return "monthly"
        return "native"

    def _estimate_tasks(self, temporal, request, mode) -> int:
        if mode in ("monthly", "annual"):
            return temporal.tasks if temporal.tasks else 0
        if request.aggregation is None and temporal.strategy in ("monthly", "annual"):
            return temporal.tasks
        return temporal.image_count

    # ================= 内部：管线 =================
    def _run_pipeline(self, record: TaskRecord, request: DownloadRequest) -> None:
        try:
            record.transition("VALIDATING")
            self.store.save(record)
            session = ensure_initialized(self.config)

            record.transition("PLANNING")
            self.store.save(record)
            plan = self.plan(request)
            record.plan = plan
            record.strategy = plan["recommended_strategy"]
            self.store.save(record)

            strategy = plan["recommended_strategy"]
            mode = plan["time_mode"]

            # 逐时间片构造影像并输出
            record.transition("SUBMITTING")
            self.store.save(record)

            outputs = self._execute_periods(record, request, plan, strategy, mode)
            files = [o["path"] for o in outputs]

            # QA
            record.transition("VALIDATING_OUTPUT")
            self.store.save(record)
            qa_files = []
            for o in outputs:
                report = validate_file(
                    o["path"],
                    expected_crs=request.crs,
                    expected_scale=request.scale_m,
                    expected_bands=o.get("stacked_band_count"),
                )
                qa_files.append(report.to_dict())
            record.files = qa_files

            # metadata.json
            dataset_dir = self._dataset_dir(request)
            meta_path = write_metadata(
                out_dir=dataset_dir,
                dataset=request.dataset,
                start_date=request.start_date,
                end_date=request.end_date,
                boundary=request.boundary,
                crs=request.crs,
                scale=request.scale_m,
                fmt=request.format,
                bands=plan.get("bands", []),
                files=files,
                plan=plan,
            )

            record.transition("COMPLETED")
            record.result = DownloadResult(
                task_id=record.task_id,
                state="COMPLETED",
                dataset=request.dataset,
                strategy=strategy,
                files=[{"path": f, "qa": q} for f, q in zip(files, qa_files)],
                metadata_path=str(meta_path),
                plan=plan,
                drive_links=record.drive_links,
                message=f"完成：{len(files)} 个文件，QA 通过 {sum(1 for q in qa_files if q['passed'])}/{len(qa_files)}",
            ).to_dict()
            self.store.save(record)
        except Exception as exc:  # noqa: BLE001
            logger.exception("任务 %s 失败", record.task_id)
            record.error = str(exc)
            try:
                record.transition("FAILED")
            except Exception:  # noqa: BLE001
                pass
            self.store.save(record)

    def _execute_periods(self, record, request, plan, strategy, mode):
        """按时间片输出 GeoTIFF。返回 [{"path": ..., "period": ...}, ...]

        stack_periods=True 时：先把各时间片下载到临时目录，
        再合并为一个多波段 GeoTIFF（波段数=时间片数，每波段=一个时间片）。
        """
        session = ensure_initialized(self.config)
        resolver = DatasetResolver()
        dsinfo = resolver.inspect(request.dataset)
        bres = BoundaryResolver()
        fc, binfo = bres.resolve(request.boundary)
        region = fc.geometry()

        base_dir = Path(request.output)
        dataset_dir = self._dataset_dir(request)
        dataset_dir.mkdir(parents=True, exist_ok=True)

        outputs: list[dict] = []

        if dsinfo.type == DatasetType.IMAGE:
            record.transition("RUNNING")
            self.store.save(record)
            image = ee.Image(request.dataset)
            path = dataset_dir / f"{safe_filename(request.description)}.tif"
            out = self._download_one(
                record, request, strategy, image, region, path,
                request.description, 0,
            )
            outputs.append({"path": str(out), "period": request.start_date})
            return outputs

        coll = build_collection(request.dataset, request.start_date, request.end_date, region)

        # 决定分组与聚合
        if mode in ("monthly", "annual"):
            aggregation = request.aggregation or "mean"
            periods = list(iter_periods(request.date_start, request.date_end, mode))
            tasks = [(key, pstart, pend, aggregation, None) for key, pstart, pend in periods]
        else:
            # native / daily：逐景（daily 可对当天聚合）
            images = list_images(coll)
            fallback_image = None
            if not images:
                # 窗口内无影像（如 MODIS 16 天合成数据对单日请求）：
                # 回退取开始日期之前的最近一景（覆盖请求日期的合成期）
                logger.warning(
                    "窗口内无影像，回退取 %s 之前的最近一景", request.start_date)
                fallback_image = (
                    ee.ImageCollection(request.dataset)
                    .filterBounds(region)
                    .filterDate("1970-01-01", request.start_date)
                    .sort("system:time_start", False)
                    .first()
                )
                tasks = [(f"{request.start_date}_nearest", None, None, None, "_NEAREST_")]
            elif len(images) > _MAX_NATIVE_TASKS:
                raise DownloadError(
                    f"逐景导出需要 {len(images)} 个任务，超过上限 {_MAX_NATIVE_TASKS}，"
                    "请使用 time_mode=monthly/annual 或指定 aggregation 减少任务数"
                )
            elif mode == "daily":
                from datetime import date as _date
                agg = request.aggregation or "mean"
                tasks = [(it.date, _date.fromisoformat(it.date), _date.fromisoformat(it.date), agg, None)
                         for it in images]
            else:
                # native：按 system:index 精确取景（filterDate 单日对 16 天合成数据会返回空集）
                tasks = [(it.date, None, None, None, it.id) for it in images]

        record.transition("RUNNING")
        self.store.save(record)

        stacking = bool(request.stack_periods) and len(tasks) > 1
        import tempfile
        stack_tmp = None
        if stacking:
            stack_tmp = Path(tempfile.mkdtemp(prefix="gee_stack_"))

        period_outputs: list[dict] = []
        for i, (key, pstart, pend, agg, img_id) in enumerate(tasks):
            if img_id == "_NEAREST_":
                image = fallback_image
            elif agg is not None:
                coll_p = coll.filterDate(pstart.isoformat(), pend.isoformat())
                if int(coll_p.size().getInfo()) == 0:
                    logger.warning("跳过无影像时间段: %s", key)
                    continue
                image = aggregate_per_period(coll_p, key, agg)
            else:
                # native：按 system:index 精确取景
                image = coll.filter(ee.Filter.eq("system:index", img_id)).first()

            if stacking:
                # 堆叠模式：先下载到临时目录，最后合并
                path = stack_tmp / f"{i:04d}_{safe_filename(key)}.tif"
                out = self._download_one(
                    record, request, strategy, image, region, path,
                    f"{request.description}_{key}", i,
                )
                period_outputs.append({"path": str(out), "period": key})
                continue

            year = key[:4]
            sub = make_output_dir(
                request.output, safe_filename(request.dataset.replace("/", "_")),
                year, allowed_roots=self.config.allowed_roots,
            )
            path = sub / f"{safe_filename(key)}.tif"
            out = self._download_one(
                record, request, strategy, image, region, path, f"{request.description}_{key}", i,
            )
            outputs.append({"path": str(out), "period": key})

        if stacking:
            outputs = self._stack_period_outputs(
                record, request, plan, period_outputs, dataset_dir, stack_tmp)

        return outputs

    # ================= 内部：时间维堆叠 =================
    def _stack_period_outputs(self, record, request, plan, period_outputs,
                              dataset_dir: Path, stack_tmp: Optional[Path]) -> list[dict]:
        """把多个时间片合并为一个多波段 GeoTIFF（波段数=时间片数）。

        输出文件名：{首时间片}-{末时间片}.tif（如 2021-01-01-2021-01-02.tif），
        每波段描述 = {波段名}_{时间片}。
        """
        import shutil
        if not period_outputs:
            raise DownloadError("没有可堆叠的时间片输出")
        dataset_dir.mkdir(parents=True, exist_ok=True)
        band_names: list[str] = plan.get("bands") or []
        labels: list[str] = []
        for po in period_outputs:
            key = po["period"]
            if band_names:
                labels.extend(f"{b}_{key}" for b in band_names)
            else:
                labels.append(key)

        if len(period_outputs) == 1:
            # 只有一个时间片：直接改名即可（退化为单波段单文件）
            src = Path(period_outputs[0]["path"])
            final = dataset_dir / f"{safe_filename(period_outputs[0]['period'])}.tif"
            shutil.move(str(src), str(final))
            if stack_tmp:
                shutil.rmtree(stack_tmp, ignore_errors=True)
            return [{"path": str(final), "period": period_outputs[0]["period"]}]

        first_key = period_outputs[0]["period"]
        last_key = period_outputs[-1]["period"]
        final = dataset_dir / f"{safe_filename(first_key)}-{safe_filename(last_key)}.tif"
        stacked = stack_period_files(
            [Path(po["path"]) for po in period_outputs],
            final,
            band_labels=labels,
        )
        if stack_tmp:
            shutil.rmtree(stack_tmp, ignore_errors=True)
        return [{
            "path": str(stacked),
            "period": f"{first_key}..{last_key}",
            "stacked": True,
            "stacked_band_count": len(labels),
        }]

    def _download_one(self, record, request, strategy, image, region, path, description, idx):
        """按策略下载单个影像，失败则抛错（任务转 FAILED）。"""
        if strategy == STRATEGY_DIRECT:
            record.transition("DOWNLOADING")
            self.store.save(record)
            return direct_download_image(
                image, region, path,
                scale=request.scale_m,
                crs=request.crs,
                config=self.config,
                bands=request.bands,
            )

        # Export Task
        spec = ExportSpec(
            description=description,
            scale=request.scale_m,
            crs=request.crs,
            region=region,
            max_pixels=self.config.default_max_pixels,
            file_format=request.format,
        )

        def on_progress(state):
            if record.gee_state != state:
                record.gee_state = state
                self.store.save(record)

        outcome = run_export_to_drive(
            image, spec,
            clip=request.clip,
            bands=request.bands,
            poll_interval_s=15.0,
            progress_cb=on_progress,
        )
        record.gee_task_id = outcome.gee_task_id
        record.gee_state = outcome.state
        self.store.save(record)

        if outcome.state != "COMPLETED":
            raise DownloadError(
                f"Earth Engine export 失败: {outcome.description} state={outcome.state} "
                f"err={outcome.error_message}"
            )

        # 从 Drive 回传本地
        try:
            record.transition("DOWNLOADING")
            self.store.save(record)
            driver = DriveDownloader()
            drive_file = driver.find_file(outcome.drive_folder, outcome.drive_name)
            if drive_file:
                return driver.download(drive_file["id"], path)
            # 找不到文件：降级返回 Drive 链接
            url = drive_web_url(outcome.drive_folder, outcome.drive_name)
            record.drive_links.append(url)
            self.store.save(record)
            raise DownloadError(
                f"Export 已完成但无法从 Drive 自动下载（{driver.error or '未找到文件'}）。"
                f"请手动下载: {url}"
            )
        except DriveDownloadError as exc:
            url = drive_web_url(outcome.drive_folder, outcome.drive_name)
            record.drive_links.append(url)
            self.store.save(record)
            raise DownloadError(
                f"Export 已完成但 Drive 回传失败: {exc}。请手动下载: {url}"
            ) from exc

    def _dataset_dir(self, request) -> Path:
        return Path(request.output) / safe_filename(request.dataset.replace("/", "_"))


def _region_area(region: ee.Geometry) -> float:
    try:
        return float(region.area(1).getInfo())
    except Exception:  # noqa: BLE001
        return 0.0


def strategy_forced_export(request) -> bool:
    """是否用户显式要求 Export（远程中转）。"""
    return (request.strategy or "auto").lower() == "export"

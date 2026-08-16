"""单元测试：direct 引擎修复（分块字节约束 / 网格对齐 / 分块缓存 / 堆叠进度 / dtype 转换）。

不依赖 GEE：只测纯函数与本地 GeoTIFF 操作。
"""

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from download.direct import (
    _align_chunk_file,
    _cache_valid,
    _grids_match,
    _prepare_cache,
    convert_output_dtype,
    stack_period_files,
)
from planner.size_estimator import (
    DEFAULT_MAX_REQUEST_BYTES,
    bytes_per_pixel,
    estimate_request_bytes,
    max_chunk_pixels_for_dtype,
)


def _make_tif(path, value=1.0, transform=None, dtype="float32", size=10):
    arr = np.full((1, size, size), value, dtype=dtype)
    with rasterio.open(
        str(path), "w", driver="GTiff",
        height=size, width=size, count=1, dtype=dtype,
        crs="EPSG:3857",
        transform=transform or from_origin(0, 100000, 1000, 1000),
        nodata=-9999.0,
    ) as dst:
        dst.write(arr)
    return path


class TestChunkByteMath:
    """P0-1：分块像素上限按 float64（8B/px）字节预算反推，而非写死 8M。"""

    def test_float64_chunk_limit_within_gee_limit(self):
        # 44MiB / 8B ≈ 5.76M px；单块请求 = 5.76M×8B ≈ 44MiB < 48MiB 上限
        n = max_chunk_pixels_for_dtype("FLOAT64")
        assert n * bytes_per_pixel("FLOAT64") <= DEFAULT_MAX_REQUEST_BYTES
        assert n * bytes_per_pixel("FLOAT64") < 48 * 1024 * 1024
        # 旧的 8M 像素假设（8M×8B=64MB）必须超限 —— 根因复现
        assert 8_000_000 * bytes_per_pixel("FLOAT64") > 48 * 1024 * 1024

    def test_dtype_aware_limits(self):
        assert max_chunk_pixels_for_dtype("FLOAT32") > max_chunk_pixels_for_dtype("FLOAT64")
        assert max_chunk_pixels_for_dtype("INT16") > max_chunk_pixels_for_dtype("FLOAT32")
        assert max_chunk_pixels_for_dtype("FLOAT64") == max_chunk_pixels_for_dtype("DOUBLE")
        # 未知 dtype 按 float64 保守处理
        assert max_chunk_pixels_for_dtype("WEIRD") == max_chunk_pixels_for_dtype("FLOAT64")

    def test_estimate_request_bytes_float64(self):
        # ~8M 像素 × 8B ≈ 64MB —— 上次 Total request size 68857425 超限的根因量级
        assert estimate_request_bytes(2828, 2828) == 2828 * 2828 * 8
        assert estimate_request_bytes(100, 100) == 80_000
        # 根因复现：整景不分块时请求 > GEE 48MiB 上限
        assert estimate_request_bytes(2828, 2828) > 48 * 1024 * 1024

    def test_bytes_per_pixel(self):
        assert bytes_per_pixel("FLOAT64") == 8
        assert bytes_per_pixel("float32") == 4
        assert bytes_per_pixel("INT16") == 2
        assert bytes_per_pixel("unknown") == 8  # 保守默认


class TestGridAlignment:
    """P1-4：GEE 返回分块网格可能与请求矩形有差异，禁止按索引直拼，需对齐。"""

    def test_grids_match(self, tmp_path):
        t = from_origin(0, 100000, 1000, 1000)
        p = _make_tif(tmp_path / "a.tif", transform=t)
        with rasterio.open(str(p)) as src:
            assert _grids_match(src, t, 10, 10, 1000) is True
            # 尺寸不同
            assert _grids_match(src, t, 11, 10, 1000) is False
            # 原点偏移（GEE 实际可能返回不同的 transform 原点）
            shifted = from_origin(500, 100500, 1000, 1000)
            assert _grids_match(src, shifted, 10, 10, 1000) is False

    def test_align_chunk_resamples_to_expected_grid(self, tmp_path):
        """分块 transform 与期望网格不一致时重采样对齐（如 1319 行 vs 1308 行场景）。"""
        expected = from_origin(0, 100000, 1000, 1000)
        shifted = from_origin(500, 100500, 1000, 1000)
        src = _make_tif(tmp_path / "chunk.tif", transform=shifted)
        dst = tmp_path / "aligned.tif"
        chunk_plan = {"x0": 0, "y0": 90000, "x1": 10000, "y1": 100000,
                      "width_px": 10, "height_px": 10}
        out = _align_chunk_file(src, dst, chunk_plan, 1000)
        assert out == dst
        with rasterio.open(str(dst)) as s:
            assert s.width == 10 and s.height == 10
            assert abs(s.transform.a - expected.a) < 1e-6
            assert abs(s.transform.e - expected.e) < 1e-6
            # 值保真（bilinear 对常量栅格无损）
            assert float(s.read(1)[0, 0]) == 1.0

    def test_align_chunk_keeps_aligned(self, tmp_path):
        """已对齐分块原样返回，不产生新文件。"""
        t = from_origin(0, 100000, 1000, 1000)
        src = _make_tif(tmp_path / "ok.tif", transform=t)
        dst = tmp_path / "unused.tif"
        chunk_plan = {"x0": 0, "y0": 90000, "x1": 10000, "y1": 100000,
                      "width_px": 10, "height_px": 10}
        assert _align_chunk_file(src, dst, chunk_plan, 1000) == src
        assert not dst.exists()


class TestChunkCache:
    """P1-5：分块缓存断点续传（参数一致复用，参数变化清空重建）。"""

    def test_manifest_matches(self, tmp_path):
        cache = tmp_path / "out.chunks"
        cache.mkdir()
        (cache / "band01_chunk00.tif").write_bytes(b"x")
        manifest = {"scale": 1000, "crs": "EPSG:3857", "bands": ["NDVI"], "chunk_count": 1}
        assert _cache_valid(cache, manifest) is False  # 无 manifest
        (cache / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        assert _cache_valid(cache, manifest) is True
        # 参数变化（如 scale 改变）-> 缓存失效
        other = dict(manifest, scale=500)
        assert _cache_valid(cache, other) is False

    def test_prepare_cache_rebuilds_on_change(self, tmp_path):
        cache = tmp_path / "out.chunks"
        manifest = {"scale": 1000, "crs": "EPSG:3857", "bands": ["NDVI"], "chunk_count": 1}
        _prepare_cache(cache, manifest)
        assert _cache_valid(cache, manifest) is True
        (cache / "band01_chunk00.tif").write_bytes(b"data")
        # 同参数再次初始化：保留已有分块（断点续传）
        _prepare_cache(cache, manifest)
        assert (cache / "band01_chunk00.tif").exists()
        # 参数变化：清空重建
        _prepare_cache(cache, dict(manifest, scale=500))
        assert not (cache / "band01_chunk00.tif").exists()
        assert _cache_valid(cache, dict(manifest, scale=500)) is True


class TestStackProgress:
    """P2-7：堆叠进度回调。"""

    def test_stack_progress_cb(self, tmp_path):
        d1 = _make_tif(tmp_path / "p1.tif", 1.0)
        d2 = _make_tif(tmp_path / "p2.tif", 2.0)
        calls = []
        stack_period_files(
            [d1, d2], tmp_path / "out.tif",
            progress_cb=lambda frac, msg: calls.append((round(frac, 4), msg)),
        )
        assert [c[0] for c in calls] == [0.5, 1.0]
        assert "堆叠中" in calls[0][1]


class TestDtypeConversion:
    """P2-10：输出 dtype 转换（float64 -> float32 + deflate，减体积）。"""

    def test_convert_float64_to_float32(self, tmp_path):
        p = _make_tif(tmp_path / "big.tif", 1.5, dtype="float64")
        before = p.stat().st_size
        out = convert_output_dtype(p, "float32")
        assert out == p
        with rasterio.open(str(p)) as src:
            assert src.dtypes[0] == "float32"
            assert float(src.read(1)[0, 0]) == pytest.approx(1.5)
        # deflate 压缩后体积应显著小于 float64 原始
        assert p.stat().st_size < before

    def test_convert_same_dtype_noop(self, tmp_path):
        p = _make_tif(tmp_path / "f32.tif", 1.0, dtype="float32")
        assert convert_output_dtype(p, "float32") == p
        with rasterio.open(str(p)) as src:
            assert src.dtypes[0] == "float32"

    def test_convert_int16(self, tmp_path):
        p = _make_tif(tmp_path / "lst.tif", 3000.0, dtype="float64")
        convert_output_dtype(p, "int16")
        with rasterio.open(str(p)) as src:
            assert src.dtypes[0] == "int16"
            assert int(src.read(1)[0, 0]) == 3000

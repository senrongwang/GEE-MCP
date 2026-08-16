"""单元测试：raster QA 与元数据（用 rasterio 生成小 GeoTIFF）。"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from raster.metadata import write_metadata
from raster.validate import validate_file


@pytest.fixture
def sample_tif(tmp_path):
    """生成一个 10x10 EPSG:3857 的 2 波段 GeoTIFF。"""
    path = tmp_path / "2021-01-01.tif"
    arr = np.zeros((2, 10, 10), dtype=np.float32)
    arr[0] = 1.0
    arr[1] = 2.0
    with rasterio.open(
        str(path), "w",
        driver="GTiff",
        height=10, width=10, count=2,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0, 100000, 1000, 1000),
        nodata=-9999.0,
    ) as dst:
        dst.write(arr)
    return path


class TestValidation:
    def test_valid_file(self, sample_tif):
        report = validate_file(
            sample_tif,
            expected_crs="EPSG:3857",
            expected_scale=1000,
            expected_bands=2,
        )
        assert report.passed is True
        summary = report.summary()
        assert "✓" in summary
        assert "EPSG:3857" in summary
        assert "Bands = 2" in summary

    def test_missing_file(self, tmp_path):
        report = validate_file(tmp_path / "nope.tif")
        assert report.passed is False
        assert any(c["check"] == "file_exists" and not c["ok"] for c in report.checks)

    def test_crs_mismatch(self, sample_tif):
        report = validate_file(sample_tif, expected_crs="EPSG:4326")
        assert report.passed is False
        crs_check = next(c for c in report.checks if c["check"] == "crs")
        assert crs_check["ok"] is False
        assert "EPSG:4326" in crs_check["detail"]


def _write_array(path, arr, transform=None):
    with rasterio.open(
        str(path), "w", driver="GTiff",
        height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
        dtype=str(arr.dtype), crs="EPSG:3857",
        transform=transform or from_origin(0, 100000, 1000, 1000),
        nodata=-9999.0,
    ) as dst:
        dst.write(arr)
    return path


class TestContentChecks:
    """P1-6：QA 只查元数据不够——增加内容级检查（非零占比 / 全 0 检测）。"""

    def test_all_zero_fails(self, tmp_path):
        """某天请求失败返回全 0 时，QA 必须拦截（此前会"通过"）。"""
        arr = np.zeros((1, 10, 10), dtype=np.float32)
        p = _write_array(tmp_path / "zero.tif", arr)
        report = validate_file(p)
        assert report.passed is False
        content = next(c for c in report.checks if c["check"] == "content")
        assert content["ok"] is False
        assert "全 0" in content["detail"]

    def test_valid_fraction_below_floor_fails(self, tmp_path):
        """非零像元占比低于 min_valid_fraction -> 失败。"""
        arr = np.zeros((1, 10, 10), dtype=np.float32)
        arr[0, 0, 0] = 1.0  # 仅 1% 非零
        p = _write_array(tmp_path / "low.tif", arr)
        report = validate_file(p, min_valid_fraction=0.5)
        assert report.passed is False

    def test_low_valid_fraction_ok_with_default_floor(self, tmp_path):
        """海洋/掩膜场景（27~35% 有效）在默认下限 1% 下通过。"""
        arr = np.zeros((1, 10, 10), dtype=np.float32)
        arr[0, 0, 0] = 1.0
        p = _write_array(tmp_path / "masked.tif", arr)
        assert validate_file(p).passed is True

    def test_content_check_skippable(self, tmp_path):
        arr = np.zeros((1, 10, 10), dtype=np.float32)
        p = _write_array(tmp_path / "skip.tif", arr)
        report = validate_file(p, check_content=False)
        assert report.passed is True  # 跳过内容检查后全 0 不再判失败

    def test_mixed_nodata_and_values(self, tmp_path):
        """部分 nodata + 部分有效值：内容检查通过。"""
        arr = np.full((1, 10, 10), -9999.0, dtype=np.float32)
        arr[0, :5, :] = 0.3  # 一半有效
        p = _write_array(tmp_path / "mix.tif", arr)
        report = validate_file(p)
        assert report.passed is True
        content = next(c for c in report.checks if c["check"] == "content")
        assert "非零像元" in content["detail"]


class TestMetadata:
    def test_write_metadata(self, tmp_path, sample_tif):
        meta_path = write_metadata(
            out_dir=tmp_path / "out",
            dataset="MODIS/061/MOD13Q1",
            start_date="2021-01-01",
            end_date="2021-12-31",
            boundary="projects/x/assets/Anhui",
            crs="EPSG:3857",
            scale=1000,
            fmt="GeoTIFF",
            bands=["NDVI", "EVI"],
            files=[sample_tif],
            plan={"strategy": "direct"},
        )
        assert meta_path.exists()
        import json
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["dataset"] == "MODIS/061/MOD13Q1"
        assert meta["crs"] == "EPSG:3857"
        assert meta["scale"] == 1000
        assert len(meta["files"]) == 1
        assert meta["files"][0]["bands"] == 2
        assert "download_time" in meta
        assert "plan" in meta


def _make_single_band_tif(path, value, transform=None, nodata=-9999.0):
    """生成 10x10 EPSG:3857 单波段 GeoTIFF。"""
    arr = np.full((1, 10, 10), value, dtype=np.float32)
    with rasterio.open(
        str(path), "w",
        driver="GTiff",
        height=10, width=10, count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=transform or from_origin(0, 100000, 1000, 1000),
        nodata=nodata,
    ) as dst:
        dst.write(arr)
    return path


class TestStackPeriodFiles:
    """时间维堆叠：多时间片 -> 一个多波段 GeoTIFF。"""

    def test_stack_two_periods(self, tmp_path):
        from download.direct import DirectDownloadError, stack_period_files
        d1 = _make_single_band_tif(tmp_path / "2021-01-01.tif", 1.0)
        d2 = _make_single_band_tif(tmp_path / "2021-01-02.tif", 2.0)
        out = stack_period_files(
            [d1, d2], tmp_path / "2021-01-01-2021-01-02.tif",
            band_labels=["NDVI_2021-01-01", "NDVI_2021-01-02"],
        )
        assert out.exists()
        with rasterio.open(str(out)) as ds:
            assert ds.count == 2
            assert ds.height == 10 and ds.width == 10
            assert ds.crs == "EPSG:3857"
            assert ds.descriptions[0] == "NDVI_2021-01-01"
            assert ds.descriptions[1] == "NDVI_2021-01-02"
            b1 = ds.read(1)
            b2 = ds.read(2)
            assert float(b1[0, 0]) == 1.0
            assert float(b2[0, 0]) == 2.0

    def test_stack_single_period_moves(self, tmp_path):
        from download.direct import stack_period_files
        d1 = _make_single_band_tif(tmp_path / "2021-01-01.tif", 1.0)
        out = stack_period_files([d1], tmp_path / "out.tif")
        assert out.exists()
        with rasterio.open(str(out)) as ds:
            assert ds.count == 1

    def test_stack_mismatched_grid_raises(self, tmp_path):
        from download.direct import DirectDownloadError, stack_period_files
        d1 = _make_single_band_tif(tmp_path / "a.tif", 1.0)
        d2 = _make_single_band_tif(
            tmp_path / "b.tif", 2.0,
            transform=from_origin(500, 100500, 1000, 1000),  # 网格偏移
        )
        with pytest.raises(DirectDownloadError):
            stack_period_files([d1, d2], tmp_path / "bad.tif")

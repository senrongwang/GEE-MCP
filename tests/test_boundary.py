"""单元测试：BoundaryResolver 的 bbox / GeoJSON 解析（GEE 构造器打桩，不访问网络）。

Asset 路径需要真实 GEE 凭据，不在此覆盖；bbox / geometry 的构造是惰性的，
这里用假 Geometry 验证解析逻辑、信息结构与优雅回退（未初始化时面积=None）。
"""

import pytest

import ee

from gee.boundary import BoundaryError, BoundaryResolver


class _FakeGeometry:
    """替身：记录 init 参数，area() 模拟未初始化 GEE 时的失败。"""

    def __init__(self, init, *args, **kwargs):
        self.init = init

    @classmethod
    def Rectangle(cls, coords):
        return cls({"type": "Rectangle", "coordinates": coords})

    def area(self, max_error=1):
        raise RuntimeError("GEE not initialized")


@pytest.fixture()
def fake_ee(monkeypatch):
    monkeypatch.setattr(ee, "Geometry", _FakeGeometry)
    return _FakeGeometry


class TestResolveBBox:
    def test_bbox_rect(self, fake_ee):
        region, info = BoundaryResolver().resolve_any(bbox=[116.0, 39.0, 118.0, 41.0])
        assert isinstance(region, _FakeGeometry)
        assert region.init["type"] == "Rectangle"
        assert region.init["coordinates"] == [116.0, 39.0, 118.0, 41.0]
        assert info.source_type == "bbox"
        assert info.type == "Rectangle"
        assert info.asset_id == "bbox:116.0,39.0,118.0,41.0"
        assert info.bounds == [[[116.0, 39.0], [118.0, 39.0], [118.0, 41.0], [116.0, 41.0], [116.0, 39.0]]]
        assert info.area_km2 is None  # GEE 未初始化时优雅回退

    def test_bbox_strings_coerced(self, fake_ee):
        region, _ = BoundaryResolver().resolve_any(bbox=["116", "39", "118", "41"])
        assert region.init["coordinates"] == [116.0, 39.0, 118.0, 41.0]

    def test_bbox_inverted(self):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any(bbox=[118.0, 41.0, 116.0, 39.0])

    def test_bbox_wrong_length(self):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any(bbox=[1, 2, 3])

    def test_bbox_out_of_range(self):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any(bbox=[116.0, 39.0, 118.0, 95.0])


class TestResolveGeometry:
    POLY = {"type": "Polygon",
            "coordinates": [[[116, 39], [118, 39], [118, 41], [116, 41], [116, 39]]]}

    def test_polygon(self, fake_ee):
        region, info = BoundaryResolver().resolve_any(geometry=self.POLY)
        assert isinstance(region, _FakeGeometry)
        assert region.init == self.POLY
        assert info.source_type == "geometry"
        assert info.type == "Polygon"
        assert info.asset_id == "geometry:Polygon"
        assert info.bounds == [[[116.0, 39.0], [118.0, 39.0], [118.0, 41.0], [116.0, 41.0], [116.0, 39.0]]]
        assert info.area_km2 is None

    def test_multipolygon(self, fake_ee):
        gj = {"type": "MultiPolygon",
              "coordinates": [[[[116, 39], [117, 39], [117, 40], [116, 40], [116, 39]]]]}
        _, info = BoundaryResolver().resolve_any(geometry=gj)
        assert info.type == "MultiPolygon"
        assert info.asset_id == "geometry:MultiPolygon"

    def test_point_rejected(self):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any(geometry={"type": "Point", "coordinates": [116, 39]})

    def test_not_dict_rejected(self):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any(geometry="POLYGON((116 39, 118 39, 118 41, 116 41, 116 39))")


class TestResolveAnyContract:
    def test_none_provided(self):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any()

    def test_empty_string_boundary_ignored(self):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any(boundary="   ")

    def test_multiple_provided(self, fake_ee):
        with pytest.raises(BoundaryError):
            BoundaryResolver().resolve_any(
                boundary="projects/x/assets/CUS",
                bbox=[116.0, 39.0, 118.0, 41.0])

"""单元测试：utils.geo（bbox / GeoJSON 校验与外包矩形，不依赖 GEE）。"""

import pytest

from utils.geo import (
    GeoValidationError,
    geojson_bounds,
    normalize_bbox,
    supported_geometry_type,
)


class TestNormalizeBBox:
    def test_valid(self):
        assert normalize_bbox([116, 39, 118, 41]) == [116.0, 39.0, 118.0, 41.0]

    def test_strings_coerced(self):
        assert normalize_bbox(["116.5", "39", "118", "41.2"]) == [116.5, 39.0, 118.0, 41.2]

    def test_not_list(self):
        with pytest.raises(GeoValidationError):
            normalize_bbox("116,39,118,41")
        with pytest.raises(GeoValidationError):
            normalize_bbox(42)

    def test_wrong_length(self):
        with pytest.raises(GeoValidationError):
            normalize_bbox([1, 2, 3])

    def test_inverted(self):
        with pytest.raises(GeoValidationError):
            normalize_bbox([118, 41, 116, 39])

    def test_equal_edges(self):
        with pytest.raises(GeoValidationError):
            normalize_bbox([116, 39, 116, 41])

    def test_out_of_range(self):
        with pytest.raises(GeoValidationError):
            normalize_bbox([116, 39, 118, 95])

    def test_non_numeric(self):
        with pytest.raises(GeoValidationError):
            normalize_bbox([116, "x", 118, 41])


class TestSupportedGeometryType:
    def test_polygon(self):
        assert supported_geometry_type({"type": "Polygon", "coordinates": [[]]}) == "Polygon"

    def test_multipolygon(self):
        assert supported_geometry_type({"type": "MultiPolygon", "coordinates": []}) == "MultiPolygon"

    def test_geometry_collection(self):
        assert supported_geometry_type(
            {"type": "GeometryCollection", "geometries": []}) == "GeometryCollection"

    def test_point_rejected(self):
        assert supported_geometry_type({"type": "Point", "coordinates": [1, 2]}) is None

    def test_not_dict(self):
        assert supported_geometry_type("x") is None
        assert supported_geometry_type(None) is None


class TestGeoJsonBounds:
    def test_polygon_bounds(self):
        gj = {"type": "Polygon",
              "coordinates": [[[116, 39], [118, 39], [118, 41], [116, 41], [116, 39]]]}
        assert geojson_bounds(gj) == [
            [[116.0, 39.0], [118.0, 39.0], [118.0, 41.0], [116.0, 41.0], [116.0, 39.0]]]

    def test_multipolygon_bounds(self):
        gj = {"type": "MultiPolygon",
              "coordinates": [[[[116, 39], [117, 39], [117, 40], [116, 40], [116, 39]]],
                              [[[117, 40], [118, 40], [118, 41], [117, 41], [117, 40]]]]}
        assert geojson_bounds(gj) == [
            [[116.0, 39.0], [118.0, 39.0], [118.0, 41.0], [116.0, 41.0], [116.0, 39.0]]]

    def test_geometry_collection_bounds(self):
        gj = {"type": "GeometryCollection", "geometries": [
            {"type": "Polygon",
             "coordinates": [[[116, 39], [117, 39], [117, 40], [116, 40], [116, 39]]]},
            {"type": "Polygon",
             "coordinates": [[[117, 40], [118, 40], [118, 41], [117, 41], [117, 40]]]},
        ]}
        assert geojson_bounds(gj) == [
            [[116.0, 39.0], [118.0, 39.0], [118.0, 41.0], [116.0, 41.0], [116.0, 39.0]]]

    def test_empty(self):
        assert geojson_bounds({"type": "Polygon", "coordinates": []}) == []

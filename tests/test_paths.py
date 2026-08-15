"""单元测试：路径安全与输出目录。"""

import pytest

from utils.paths import (
    PathNotAllowedError,
    ensure_allowed_root,
    make_output_dir,
    resolve_output_path,
    safe_filename,
)


class TestPathSafety:
    def test_inside_root_ok(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        p = ensure_allowed_root(root / "a" / "b.tif", [root])
        assert p == (root / "a" / "b.tif")

    def test_outside_root_raises(self, tmp_path):
        root = tmp_path / "data"
        other = tmp_path / "elsewhere"
        other.mkdir()
        with pytest.raises(PathNotAllowedError):
            ensure_allowed_root(other / "x.tif", [root])

    def test_path_traversal_rejected(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        with pytest.raises(PathNotAllowedError):
            ensure_allowed_root(root / ".." / "escape.tif", [root])

    def test_resolve_creates_dirs(self, tmp_path):
        root = tmp_path / "data"
        p = resolve_output_path(root, "MODIS", "2021", allowed_roots=[root])
        assert p.is_dir()

    def test_make_output_dir_layout(self, tmp_path):
        root = tmp_path / "GEE_Data"
        p = make_output_dir(root, "MODIS_061_MOD13Q1", "2021", allowed_roots=[root])
        assert p == root / "MODIS_061_MOD13Q1" / "2021"
        assert p.is_dir()


class TestSafeFilename:
    def test_cleans_chars(self):
        assert ":" not in safe_filename("a:b|c?d")
        assert safe_filename("a/b") == "a_b"

    def test_empty_fallback(self):
        assert safe_filename("   ") == "output"

    def test_reserved_name(self):
        assert safe_filename("CON").startswith("_")

    def test_keeps_extension(self):
        assert safe_filename("2021-01-01.tif") == "2021-01-01.tif"

"""单元测试：gee_help 帮助文档与参数提醒。"""

from server import _DOWNLOAD_REQUIRED_PARAMS, _HELP_DOC

_REQUIRED_KEYS = ["dataset", "start_date", "end_date", "边界（boundary / bbox / geometry 三选一）"]


class TestHelpDoc:
    def test_overview_contains_required_and_optional(self):
        doc = _HELP_DOC()
        assert "gee_download 必选参数" in doc
        assert "gee_download 可选参数" in doc
        required = doc["gee_download 必选参数"]
        assert set(required) == set(_REQUIRED_KEYS)
        assert "output" in doc["gee_download 可选参数"]
        assert "dry_run" in doc["gee_download 可选参数"]
        assert "bands" in doc["gee_download 可选参数"]
        # 新增：bbox / geometry 也在可选参数与边界说明中
        assert "bbox" in doc["gee_download 可选参数"]
        assert "geometry" in doc["gee_download 可选参数"]
        assert "bbox" in required["边界（boundary / bbox / geometry 三选一）"]

    def test_topics(self):
        assert "调用流程" in _HELP_DOC("workflow")
        assert "示例" in _HELP_DOC("examples")
        assert "工具" in _HELP_DOC("login")
        assert "工具" in _HELP_DOC("dataset_info")
        d = _HELP_DOC("download")
        assert "gee_download 必选参数" in d and "gee_download 可选参数" in d

    def test_search_topic(self):
        doc = _HELP_DOC("search")
        assert doc["工具"]  # 非空
        assert "数据集发现流程" in doc
        assert "gee_search_datasets 参数" in doc

    def test_validate_and_catalog_topics(self):
        for topic in ("validate", "catalog_update"):
            doc = _HELP_DOC(topic)
            assert "工具" in doc
            assert "数据集发现流程" in doc

    def test_overview_contains_new_tools(self):
        doc = _HELP_DOC()
        tools = doc["工具清单"]
        for name in ("gee_search_datasets", "gee_validate_dataset", "gee_catalog_update"):
            assert name in tools
        assert "gee_search_datasets 参数" in doc
        assert "数据集发现流程" in doc

    def test_unknown_topic_returns_overview_with_warning(self):
        doc = _HELP_DOC("bogus")
        assert "警告" in doc
        assert "gee_download 必选参数" in doc

    def test_required_params_constant(self):
        assert list(_DOWNLOAD_REQUIRED_PARAMS) == _REQUIRED_KEYS


class TestValidationAdvice:
    def test_advice_mentions_required_and_help(self):
        # 与 server.py 中校验失败时的提示保持一致
        advice = (
            "gee_download 必选参数：dataset / start_date / end_date / "
            "边界（boundary|bbox|geometry 三选一）；"
            "常用可选参数：output / scale / crs / bands / dry_run。\n"
            f"必选参数说明：{'；'.join(f'{k}（{v}）' for k, v in _DOWNLOAD_REQUIRED_PARAMS.items())}。\n"
            "可调用 gee_help(topic='download') 查看完整说明。"
        )
        assert "必选参数" in advice
        assert "gee_help(topic='download')" in advice
        assert "dataset" in advice

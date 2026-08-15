"""单元测试：gee_help 帮助文档与参数提醒。"""

from server import _DOWNLOAD_REQUIRED_PARAMS, _HELP_DOC


class TestHelpDoc:
    def test_overview_contains_required_and_optional(self):
        doc = _HELP_DOC()
        assert "gee_download 必选参数" in doc
        assert "gee_download 可选参数" in doc
        required = doc["gee_download 必选参数"]
        assert set(required) == {"dataset", "start_date", "end_date", "boundary"}
        assert "output" in doc["gee_download 可选参数"]
        assert "dry_run" in doc["gee_download 可选参数"]
        assert "bands" in doc["gee_download 可选参数"]

    def test_topics(self):
        assert "调用流程" in _HELP_DOC("workflow")
        assert "示例" in _HELP_DOC("examples")
        assert "工具" in _HELP_DOC("login")
        assert "工具" in _HELP_DOC("dataset_info")
        d = _HELP_DOC("download")
        assert "gee_download 必选参数" in d and "gee_download 可选参数" in d

    def test_unknown_topic_returns_overview_with_warning(self):
        doc = _HELP_DOC("bogus")
        assert "警告" in doc
        assert "gee_download 必选参数" in doc

    def test_required_params_constant(self):
        assert list(_DOWNLOAD_REQUIRED_PARAMS) == ["dataset", "start_date", "end_date", "boundary"]


class TestValidationAdvice:
    def test_advice_mentions_required_and_help(self):
        # 与 server.py 中校验失败时的提示保持一致
        advice = (
            "gee_download 必选参数：dataset / start_date / end_date / boundary；"
            "常用可选参数：output / scale / crs / bands / dry_run。\n"
            f"必选参数说明：{'；'.join(f'{k}（{v}）' for k, v in _DOWNLOAD_REQUIRED_PARAMS.items())}。\n"
            "可调用 gee_help(topic='download') 查看完整说明。"
        )
        assert "必选参数" in advice
        assert "gee_help(topic='download')" in advice
        assert "dataset" in advice

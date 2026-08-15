"""日志工具：禁止记录任何凭据 / token / 私密信息（设计文档第 37 节）。"""

from __future__ import annotations

import logging
import re
import sys

_CONFIGURED = False


def get_logger(name: str = "ai_gee") -> logging.Logger:
    return logging.getLogger(name)


class SecretRedactor(logging.Filter):
    """把日志中的疑似 token / 凭据内容打码。"""

    _PATTERNS = [
        re.compile(r"(?i)(authorization|bearer)\s+[A-Za-z0-9._~+/\-=]{8,}"),
        re.compile(r"(?i)(token|credential|secret|password|key)\s*[=:]\s*\S+"),
        re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
        re.compile(r"ya29\.[0-9A-Za-z\-_]+"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        for pat in self._PATTERNS:
            msg = pat.sub("[REDACTED]", msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(level: str = "INFO", redact_secrets: bool = True) -> None:
    """初始化根日志。可安全重复调用。"""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    ))
    root.addHandler(handler)
    if redact_secrets:
        root.addFilter(SecretRedactor())
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    _CONFIGURED = True

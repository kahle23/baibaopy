"""
兼容 shim：``dom_summary`` 已拆分至 :mod:`baibao.autotest.probe`。

保留旧导入路径（``from baibao.autotest.dom_summary import run_probe`` 等），后续请逐步切换到 ``baibao.autotest.probe``。
除公共名外，也 re-export 测试与脚本在用的 ``EXTRACT_JS`` / ``MAX_OUTPUT_CHARS`` / ``format_custom``。
"""

from .probe import (
    EXTRACT_JS,
    MAX_OUTPUT_CHARS,
    build_target_url,
    extract_summary,
    format_summary,
    run_probe,
)
from .probe.render import format_custom

__all__ = [
    "EXTRACT_JS",
    "MAX_OUTPUT_CHARS",
    "build_target_url",
    "extract_summary",
    "format_custom",
    "format_summary",
    "run_probe",
]

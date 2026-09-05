"""
probe — DOM 摘要探针（原 ``dom_summary.py`` 拆分而来）。

把已登录页面压缩成 KB 级 markdown 结构摘要，供 AI 不读整页 HTML 了解页面：

  - :func:`run_probe` — 完整流程入口（登录态缓存 + 浏览器 + 打开页面 + 提取）
  - :class:`ProbeOptions` + :func:`run_probe_with` — 参数对象版入口（编程调用推荐）
  - :func:`extract_summary` / :func:`format_summary` — 提取与渲染
  - :func:`build_target_url` — 目标地址规范化（Git Bash MSYS 污染拦截）

旧导入路径 ``baibao.autotest.dom_summary`` 由根目录 shim 继续保底。
"""

from .extract_js import EXTRACT_JS
from .render import MAX_OUTPUT_CHARS, format_summary
from .runner import ProbeOptions, extract_summary, run_probe, run_probe_with
from .url import build_target_url

__all__ = [
    "EXTRACT_JS",
    "MAX_OUTPUT_CHARS",
    "ProbeOptions",
    "build_target_url",
    "extract_summary",
    "format_summary",
    "run_probe",
    "run_probe_with",
]

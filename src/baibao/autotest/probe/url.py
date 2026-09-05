"""
url — 探针目标地址规范化（纯函数）。

把 CLI/脚本传入的 ``target``（完整 URL / hash 路由 / 纯路由）规范成可直接 ``page.goto`` 的 URL，并拦截 Git Bash (MSYS) 路径转换污染。
"""

from __future__ import annotations

import re

from ..core.envutil import normalize_base_url

__all__ = ["build_target_url"]

# Git Bash (MSYS) 会把以 / 开头的参数转换成 Windows 路径：
# "#/it-asset" → "#C:/Program Files/Git/it-asset"。检测到被污染的形式直接报错并给解法。
_MSYS_POLLUTED = re.compile(r"^#?[A-Za-z]:[\\/]")


def build_target_url(target: str, base_url: str) -> str:
    """
    把 ``target`` 规范成完整 URL。

    支持三种写法：

      - 完整 URL（``http``/``https`` 开头）原样返回；
      - hash 路由（``#/it-asset``）拼到 ``base_url`` 后；
      - 纯路由（``it-asset`` 或 ``/it-asset``）自动补 ``#``——**Git Bash 下推荐**，天然免疫 MSYS 参数路径转换。

    Raises:
        RuntimeError: ``target`` 是被 Git Bash MSYS 路径转换污染的形式（如 ``#C:/Program Files/Git/it-asset``）。
    """
    if _MSYS_POLLUTED.match(target):
        raise RuntimeError(
            f"目标路由疑似被 Git Bash 路径转换污染：{target!r}。"
            "请改用纯路由（如 it-asset，不带开头 # 或 /），"
            "或加 MSYS_NO_PATHCONV=1 前缀，或传完整 URL",
        )
    if target.startswith("http"):
        return target
    if not target.startswith("#"):
        target = "#" + (target if target.startswith("/") else f"/{target}")
    return f"{normalize_base_url(base_url)}/{target}"

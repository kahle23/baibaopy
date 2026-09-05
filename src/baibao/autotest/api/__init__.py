"""
api — 后端接口基类（ApiBase，复用浏览器登录态 cookie 的 HTTP 调用骨架）。

包接管原 ``baibao.autotest.api`` 模块名，旧导入路径不变。
"""

from .base import ApiBase

__all__ = ["ApiBase"]

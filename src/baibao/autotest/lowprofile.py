"""
兼容 shim：``lowprofile`` 实现位于 :mod:`baibao.autotest.core.lowprofile`。

保留与 ``from baibao.autotest.browser import launch_browser`` 同风格的扁平导入路径
（``from baibao.autotest.lowprofile import human_pause, risk_wall_hit``），
新路径为 :mod:`baibao.autotest.core.lowprofile`。
"""

from .core.lowprofile import *
from .core.lowprofile import __all__  # noqa: F401

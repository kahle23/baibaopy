"""
兼容 shim：``browser`` 已迁移至 :mod:`baibao.autotest.core.browser`。

保留旧导入路径（``from baibao.autotest.browser import launch_browser``），后续请逐步切换到新路径。
"""

from .core.browser import *
from .core.browser import __all__  # noqa: F401

"""
兼容 shim：``login_state`` 已迁移至 :mod:`baibao.autotest.core.login_state`。

保留旧导入路径（``from baibao.autotest.login_state import LoginCfg`` 等），后续请逐步切换到新路径。
"""

from .core.login_state import *
from .core.login_state import __all__  # noqa: F401

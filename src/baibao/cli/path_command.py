"""
Python 路径命令 - 将 Python 安装目录和 Scripts 目录添加到 PATH 环境变量。
"""

import os
import platform
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.envinfo import pyinfo
from pykunlun.util import logutil

from baibao.system import env_var as sys_env

log = logutil.getLogger(__name__)


class PythonPathSetupCommand(Command):
    """
    将 Python 安装目录和 Scripts 目录添加到 PATH 环境变量。

    创建 PYTHON_HOME 和 PYTHON_SCRIPT_HOME 环境变量，
    然后将变量引用添加到 PATH 中，实现跨平台的 Python 路径配置。
    """

    # 环境变量名称
    PYTHON_HOME_VAR = "PYTHON_HOME"
    SCRIPT_HOME_VAR = "PYTHON_SCRIPT_HOME"

    @property
    def name(self) -> str:
        return "python_path_setup"

    @property
    def description(self) -> str:
        return "设置 Python 安装目录和 Scripts 目录到 PATH 环境变量"

    @property
    def usage(self) -> str:
        return "python -m baibao python_path_setup [--force]"

    # region ======== 私有方法 ========
    def _confirm_action(self, action: str) -> bool:
        """
        确认用户操作。

        Args:
            action: 要确认的操作描述。

        Returns:
            用户确认返回 True，否则返回 False。
        """
        try:
            response = input(f"是否{action}？(y/N): ").strip().lower()
            return response in ('y', 'yes')
        except (EOFError, KeyboardInterrupt):
            return False

    def _setup_env_var(
        self,
        var_name: str,
        expected_value: str,
        force: bool,
        var_desc: str,
    ) -> bool:
        """
        设置环境变量并添加到 PATH。

        Args:
            var_name: 环境变量名称。
            expected_value: 期望的环境变量值。
            force: 是否强制覆盖。
            var_desc: 变量描述（用于日志）。

        Returns:
            设置成功返回 True，失败返回 False。
        """
        svc = sys_env.env_var_manager.get_service()
        # 检查环境变量是否已存在
        current_value = svc.get_var(var_name)
        if current_value:
            if current_value == expected_value:
                log.info("环境变量 %s 已存在且路径相同", var_name)
            else:
                log.warning("环境变量 %s 已存在，当前值: %s", var_name, current_value)
                if not force and not self._confirm_action(f"覆盖现有{var_desc}"):
                    log.info("跳过 %s 设置", var_name)
                    return True  # 跳过不算失败
                # 设置环境变量
                if not svc.set_var(var_name, expected_value):
                    log.error("设置环境变量 %s 失败", var_name)
                    return False
                log.info("[OK] 已设置环境变量 %s=%s", var_name, expected_value)
        else:
            # 环境变量不存在，直接设置
            if not svc.set_var(var_name, expected_value):
                log.error("设置环境变量 %s 失败", var_name)
                return False
            log.info("[OK] 已设置环境变量 %s=%s", var_name, expected_value)

        # 将路径值添加到 PATH
        if not svc.append_to_path(expected_value):
            log.error("添加 %s 到 PATH 失败", expected_value)
            return False

        log.info("[OK] 已将 %s 添加到 PATH", expected_value)

        return True
    # endregion

    # region ======== 公共方法 ========
    def execute(self, ctx: CliContext) -> Any:
        """
        执行命令，将 Python 安装目录和 Scripts 目录添加到 PATH。

        Args:
            ctx: 当前命令行上下文；命令参数经 ``ctx.current_args`` 获取。

        Returns:
            执行成功返回 True，失败返回 False。
        """
        args = ctx.current_args
        # 解析参数
        force = "--force" in args

        # 获取 Python 安装目录
        python_home = pyinfo.get_python_home()

        log.info("Python 安装目录: %s", python_home)

        # 获取 Scripts 目录
        scripts_dir = pyinfo.get_python_bin_dir()

        # 检查 Scripts 目录是否存在
        if not os.path.isdir(scripts_dir):
            log.warning("Scripts 目录不存在: %s", scripts_dir)
            log.info("是否需要创建该目录？")
            if not self._confirm_action("创建目录"):
                log.info("操作已取消")
                return False
            try:
                os.makedirs(scripts_dir, exist_ok=True)
                log.info("[OK] 已创建目录: %s", scripts_dir)
            except OSError as e:
                log.error("创建目录失败: %s", e)
                return False

        log.info("Scripts 目录: %s", scripts_dir)

        # 获取当前系统
        system = platform.system().lower()
        log.info("操作系统: %s", system)

        # 设置 PYTHON_HOME
        log.info("")
        log.info("======== 设置 PYTHON_HOME ========")
        if not self._setup_env_var(
            self.PYTHON_HOME_VAR, python_home, force, "PYTHON_HOME"
        ):
            return False

        # 设置 PYTHON_SCRIPT_HOME
        log.info("")
        log.info("======== 设置 PYTHON_SCRIPT_HOME ========")
        if not self._setup_env_var(
            self.SCRIPT_HOME_VAR, scripts_dir, force, "PYTHON_SCRIPT_HOME"
        ):
            return False

        # 提示用户需要重启终端
        log.info("")
        log.info("======== 配置完成 ========")
        log.info("请重启终端或执行以下命令使环境变量生效：")
        if system == "windows":
            log.info("  set %s=%s", self.PYTHON_HOME_VAR, python_home)
            log.info("  set %s=%s", self.SCRIPT_HOME_VAR, scripts_dir)
            log.info("  set PATH=%%PATH%%;%s;%s", python_home, scripts_dir)
        else:
            log.info("  export %s=%s", self.PYTHON_HOME_VAR, python_home)
            log.info("  export %s=%s", self.SCRIPT_HOME_VAR, scripts_dir)
            log.info("  export PATH=$PATH:%s:%s", python_home, scripts_dir)

        return True
    # endregion

"""
Windows 平台的环境变量管理实现。

通过 HKLM 注册表（``SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment``）
实现永久生效的环境变量管理；优先调用 PowerShell，失败回退到 ``reg`` 命令。
写入系统环境变量需要管理员权限。
"""

import os
import subprocess

from pykunlun.system import EnvVarService, env_var
from pykunlun.util import logutil

log = logutil.getLogger(__name__)

# 系统环境变量注册表路径
_HKLM_ENV_PATH = r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


class WindowsEnvService(EnvVarService):
    """
    Windows 环境变量管理实现。

    通过 PowerShell ``[Environment]::SetEnvironmentVariable(..., target)``
    写入环境变量：系统级（``Machine``）写入 HKLM、用户级（``User``）写入 HKCU。
    系统级 PowerShell 写入失败时回退到 ``reg`` 命令。系统级写入需管理员权限。
    """

    def __init__(self, platform: str = "windows") -> None:
        super().__init__(platform)

    # region ======== 私有辅助 ========
    @staticmethod
    def _run_powershell(command: str) -> str | None:
        """
        执行 PowerShell 命令。

        Args:
            command: 要执行的 PowerShell 命令字符串。

        Returns:
            命令的 stdout（成功时可能为空串）；返回码非 0、抛出异常或
            PowerShell 不可用时返回 None。
        """
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True, text=True, check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode == 0 else None

    @staticmethod
    def _set_system_var_via_reg(name: str, value: str) -> bool:
        """
        通过 ``reg add`` 写入系统级环境变量。

        作为 PowerShell 不可用时的回退方案，仅支持系统级（HKLM）。

        Args:
            name: 环境变量名。
            value: 环境变量值。

        Returns:
            写入成功返回 True，失败返回 False。
        """
        try:
            result = subprocess.run(
                ["reg", "add", _HKLM_ENV_PATH, "/v", name, "/t", "REG_SZ", "/d", value, "/f"],
                capture_output=True, text=True, check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    @staticmethod
    def _read_system_path() -> str:
        """
        读取系统级 PATH 环境变量。

        优先使用 PowerShell，失败回退到 ``reg query``。

        Returns:
            系统 PATH 字符串；读取失败返回空字符串。
        """
        # 优先使用 PowerShell
        out = WindowsEnvService._run_powershell(
            "[Environment]::GetEnvironmentVariable('Path', 'Machine')"
        )
        if out is not None:
            return out.strip()

        # 回退到 reg query
        result = subprocess.run(
            ["reg", "query", _HKLM_ENV_PATH, "/v", "Path"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "Path" in line and "REG_" in line:
                    parts = line.split("    ")
                    if len(parts) >= 3:
                        return parts[2].strip()
        return ""

    @staticmethod
    def _write_system_path(new_path: str) -> bool:
        """
        写入系统级 PATH 环境变量。

        优先使用 PowerShell，失败回退到 ``reg add``。

        Args:
            new_path: 新的 PATH 字符串。

        Returns:
            写入成功返回 True，失败返回 False。
        """
        # 优先使用 PowerShell
        ps_cmd = f'[Environment]::SetEnvironmentVariable("Path", "{new_path}", "Machine")'
        if WindowsEnvService._run_powershell(ps_cmd) is not None:
            return True

        # 回退到 reg add
        result = subprocess.run(
            ["reg", "add", _HKLM_ENV_PATH, "/v", "Path", "/t", "REG_EXPAND_SZ", "/d", new_path, "/f"],
            capture_output=True, text=True, check=False,
        )
        return result.returncode == 0

    @staticmethod
    def _add_to_process_path(value: str) -> None:
        """
        将值追加到当前进程的 PATH。

        Args:
            value: 要追加的路径。
        """
        os.environ["PATH"] = f"{os.environ.get('PATH', '')};{value}"

    @staticmethod
    def _remove_from_process_path(value: str) -> None:
        """
        从当前进程的 PATH 移除指定值。

        Args:
            value: 要移除的路径。
        """
        path_list = [p for p in os.environ.get("PATH", "").split(";") if p and p != value]
        os.environ["PATH"] = ";".join(path_list)
    # endregion

    # region ======== 策略接口实现 ========
    def set_var(self, name: str, value: str, scope: int | None = None) -> bool:
        # scope：None / SCOPE_SYSTEM(1) -> 'Machine'；SCOPE_USER(2) -> 'User'
        target = 'User' if scope == env_var.SCOPE_USER else 'Machine'
        # 优先使用 PowerShell
        ps_cmd = f'[Environment]::SetEnvironmentVariable("{name}", "{value}", "{target}")'
        if WindowsEnvService._run_powershell(ps_cmd) is not None:
            os.environ[name] = value
            return True

        # reg 回退仅支持系统级（HKLM）；用户级依赖 PowerShell
        if target == 'Machine' and self._set_system_var_via_reg(name, value):
            os.environ[name] = value
            return True
        return False

    def append_to_path(self, value: str) -> bool:
        try:
            # 查询系统 PATH
            current_path = self._read_system_path()
            parts = [p for p in current_path.split(";") if p]

            # 已存在则跳过
            if value in parts:
                log.info("PATH 中已包含 %s", value)
                return True

            # 拼接新 PATH
            new_path = f"{current_path};{value}" if current_path else value

            # 写入系统 PATH
            if not self._write_system_path(new_path):
                log.error("设置系统 PATH 失败")
                return False

            # 同步到当前进程
            self._add_to_process_path(value)
            return True

        except FileNotFoundError:
            log.error("reg 命令不可用，请确保在 Windows 系统上运行")
            return False
        except subprocess.SubprocessError as e:
            log.error("添加到 Windows PATH 时出错: %s", e)
            return False

    def remove_from_path(self, value: str) -> bool:
        try:
            # 查询系统 PATH
            current_path = self._read_system_path()
            parts = [p for p in current_path.split(";") if p]

            # 不存在则跳过
            if value not in parts:
                log.info("PATH 中不包含 %s", value)
                return True

            # 移除指定值
            new_path = ";".join(p for p in parts if p != value)

            # 写入系统 PATH
            if not self._write_system_path(new_path):
                log.error("设置系统 PATH 失败")
                return False

            # 同步到当前进程
            self._remove_from_process_path(value)

            return True

        except FileNotFoundError:
            log.error("reg 命令不可用，请确保在 Windows 系统上运行")
            return False
        except subprocess.SubprocessError as e:
            log.error("从 Windows PATH 移除时出错: %s", e)
            return False
    # endregion

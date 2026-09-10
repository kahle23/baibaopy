"""
Unix-like 平台（Linux、macOS、BSD 等）的环境变量管理实现。

通过向用户 shell 配置文件（如 ``~/.bashrc``、``~/.zshrc``、``~/.profile``）
追加 ``export`` 行实现永久生效的环境变量管理。
"""

import os

from pykunlun.envinfo import osenv
from pykunlun.system import EnvVarService, env_var
from pykunlun.util import logutil

log = logutil.getLogger(__name__)


class UnixEnvService(EnvVarService):
    """
    Unix-like 系统环境变量管理实现。

    依据 ``SHELL`` 环境变量推断当前 shell 配置文件，
    通过追加/过滤 ``export`` 行实现永久生效。

    仅支持用户级（``SCOPE_USER``）环境变量：Unix 下系统级配置需 root 写入
    ``/etc/environment`` 等文件，本实现不涉及；传入 ``scope=SCOPE_SYSTEM (1)`` 将抛出
    ``ValueError``。
    """

    def __init__(self, platform: str = "unix") -> None:
        super().__init__(platform)

    # region ======== 私有辅助 ========
    @staticmethod
    def _build_export_line(name: str, value: str) -> str:
        """
        构建环境变量的 ``export`` 行。

        Args:
            name: 环境变量名。
            value: 环境变量值。

        Returns:
            形如 ``export NAME="VALUE"`` 的字符串。
        """
        return f'export {name}="{value}"'

    @staticmethod
    def _build_path_append_line(value: str) -> str:
        """
        构建 PATH 追加的 ``export`` 行。

        Args:
            value: 要追加到 PATH 的路径。

        Returns:
            形如 ``export PATH="$PATH:VALUE"`` 的字符串。
        """
        return f'export PATH="$PATH:{value}"'

    @staticmethod
    def _read_profile(path: str) -> str | None:
        """
        读取配置文件全部内容。

        Args:
            path: 配置文件路径。

        Returns:
            文件全部内容；文件不存在或读取失败时返回 None。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.error("读取配置文件失败: %s", e)
            return None

    @staticmethod
    def _read_profile_lines(path: str) -> list[str] | None:
        """
        读取配置文件的行列表。

        Args:
            path: 配置文件路径。

        Returns:
            行列表；文件不存在或读取失败时返回 None。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.readlines()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.error("读取配置文件失败: %s", e)
            return None

    @staticmethod
    def _append_to_profile(path: str, text: str) -> bool:
        """
        向配置文件追加文本。

        Args:
            path: 配置文件路径。
            text: 要追加的文本。

        Returns:
            写入成功返回 True，失败返回 False。
        """
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
            return True
        except OSError as e:
            log.error("写入配置文件失败: %s", e)
            return False

    @staticmethod
    def _rewrite_profile(path: str, lines: list[str]) -> bool:
        """
        用给定行列表重写配置文件。

        Args:
            path: 配置文件路径。
            lines: 要写入的行列表。

        Returns:
            写入成功返回 True，失败返回 False。
        """
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return True
        except OSError as e:
            log.error("写入配置文件失败: %s", e)
            return False

    @staticmethod
    def _add_to_process_path(value: str) -> None:
        """
        将值追加到当前进程的 PATH。

        Args:
            value: 要追加的路径。
        """
        os.environ["PATH"] = f"{os.environ.get('PATH', '')}:{value}"

    @staticmethod
    def _remove_from_process_path(value: str) -> None:
        """
        从当前进程的 PATH 移除指定值。

        Args:
            value: 要移除的路径。
        """
        path_list = [p for p in os.environ.get("PATH", "").split(":") if p and p != value]
        os.environ["PATH"] = ":".join(path_list)
    # endregion

    # region ======== 策略接口实现 ========
    def set_var(self, name: str, value: str, scope: int | None = None) -> bool:
        if scope == env_var.SCOPE_SYSTEM:
            raise ValueError("Unix 暂不支持系统级环境变量（需 root 写入 /etc/environment）")
        profile_path = osenv.get_shell_profile_path()
        if not profile_path:
            return False
        export_line = self._build_export_line(name, value)
        # 已存在相同配置则跳过写入，仅同步到当前进程
        content = self._read_profile(profile_path)
        if content is not None and export_line in content:
            os.environ[name] = value
            return True
        if not self._append_to_profile(profile_path, f"\n{export_line}\n"):
            return False
        os.environ[name] = value
        return True

    def append_to_path(self, value: str) -> bool:
        config_file = osenv.get_shell_profile_path()
        if not config_file:
            return False

        # 检查是否已存在
        content = self._read_profile(config_file)
        if content is not None and value in content and "PATH" in content:
            log.info("PATH 中已包含 %s", value)
            return True

        # 追加到配置文件
        path_line = self._build_path_append_line(value)
        if not self._append_to_profile(config_file, f"\n# Add to PATH\n{path_line}\n"):
            return False

        # 同步到当前进程
        self._add_to_process_path(value)
        return True

    def remove_from_path(self, value: str) -> bool:
        config_file = osenv.get_shell_profile_path()
        if not config_file:
            return False

        # 配置文件不存在视为无可移除项
        if not os.path.exists(config_file):
            log.info("配置文件不存在: %s", config_file)
            return True

        # 读取并过滤掉包含该值的 PATH 相关行
        lines = self._read_profile_lines(config_file)
        if lines is None:
            return False
        new_lines = [line for line in lines if not (value in line and "PATH" in line)]

        # 写回配置文件
        if not self._rewrite_profile(config_file, new_lines):
            return False

        # 同步到当前进程
        self._remove_from_process_path(value)
        return True
    # endregion

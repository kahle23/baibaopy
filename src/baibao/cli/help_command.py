"""
Baibao help 命令模块。

提供 BaibaoHelpCommand，在默认帮助信息基础上补充项目简介与常用命令示例。
"""

from typing import Any

from pykunlun.cli import HelpCommand


class BaibaoHelpCommand(HelpCommand):
    """
    Baibao 帮助命令。

    重写 full_help_text，在标准帮助文本头部追加项目简介、尾部追加常用命令示例。
    """

    def full_help_text(self, commands: dict[str, Any]) -> str:
        """
        生成所有命令的帮助文本。

        在标准帮助文本基础上，头部追加 BaiBao 项目简介，尾部追加常用命令示例。

        Args:
            commands: 已注册的命令字典，键为命令名称，值为命令实例。

        Returns:
            生成的完整帮助文本。
        """
        # 构建命令列表
        command_lines: list[str] = []
        # 计算所有命令名称的最大长度，用于对齐
        max_name_len = max(len(cmd.name) for cmd in commands.values()) if commands else 0
        for cmd in commands.values():
            command_lines.append(f"    {cmd.name:<{max_name_len}} {cmd.description}")
        # 拼接命令列表
        commands_text = "\n".join(command_lines)
        # 构建帮助文本（头部增加 baibao 描述，尾部增加常用示例）
        return (
            f"BaiBao - 百宝，方便好用的 Python 常用功能库\n\n"
            f"可用命令:\n"
            f"{commands_text}\n\n"
            f"使用 {self.usage} 查看具体命令的详细用法\n\n"
            f"常用示例:\n"
            f"    python -m baibao --version            查看版本号\n"
            f"    python -m baibao help pip_install     查看 pip_install 命令的详细用法\n"
            f"    python -m baibao pip_install <包名>   安装指定的 Python 包\n"
            f"    python -m baibao pip_upgrade <包名>   升级指定的 Python 包\n"
        )

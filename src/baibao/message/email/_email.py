"""
邮件发送模块。

提供统一的邮件发送接口。
通过邮件配置名获取对应的 EmailClient 实例进行操作。
"""

import threading
from typing import Any

from .email_client import EmailCfg, EmailClient, EmailSendResult

# 存储不同配置名对应的 EmailClient 实例
_clients: dict[str, EmailClient] = {}
_clients_lock = threading.RLock()

# 默认配置名
DEFAULT_CFG_NAME = "default"


def get_client(cfg_name: str | None = None) -> EmailClient:
    """
    获取指定配置名对应的 EmailClient 实例。

    对于默认配置名，如果尚未设置，会自动从 ./email.config 文件加载配置并初始化客户端。

    Args:
        cfg_name: 邮件配置名，如果不传则使用默认配置名。

    Returns:
        EmailClient 实例。

    Raises:
        KeyError: 指定的配置名对应的 EmailClient 不存在时抛出。
    """
    # 如果未指定配置名，使用默认配置名
    if not cfg_name:
        cfg_name = DEFAULT_CFG_NAME
    # 使用锁保护全局字典的访问
    with _clients_lock:
        # 如果配置名不存在，并且是默认配置名，尝试从 ./email.config 加载配置
        if cfg_name not in _clients:
            if cfg_name == DEFAULT_CFG_NAME:
                try:
                    cfg = EmailCfg.load_from_json_cfg("./email.config")
                    set_client(cfg_name, cfg)
                except FileNotFoundError as e:
                    raise KeyError("自动加载默认配置失败: 找不到配置文件 './email.config'") from e
                except ValueError as e:
                    raise KeyError(f"自动加载默认配置失败: 配置格式错误 - {e}") from e
                except Exception as e:
                    raise KeyError(f"自动加载默认配置失败: {e}") from e
            else:
                raise KeyError(f"未找到配置名 '{cfg_name}' 对应的 EmailClient，请先调用 set_client() 设置")
        # 返回对应的 EmailClient 实例
        return _clients[cfg_name]


def set_client(cfg_name: str, client: EmailClient | EmailCfg | dict[str, Any]) -> None:
    """
    设置指定配置名对应的 EmailClient 或 EmailCfg。

    如果传入 EmailCfg 或 dict，会自动创建 EmailClient 实例。

    Args:
        cfg_name: 邮件配置名。
        client: EmailClient 实例、EmailCfg 配置对象或配置字典。
    """
    # 如果未指定配置名，使用默认配置名
    if not cfg_name:
        cfg_name = DEFAULT_CFG_NAME
    # 使用锁保护全局字典的访问
    with _clients_lock:
        # 设置对应的 EmailClient 实例
        if isinstance(client, EmailClient):
            _clients[cfg_name] = client
        elif isinstance(client, EmailCfg):
            _clients[cfg_name] = EmailClient(client)
        elif isinstance(client, dict):
            _clients[cfg_name] = EmailClient(EmailCfg(**client))
        else:
            raise TypeError(f"client 必须是 EmailClient、EmailCfg 或 dict 类型，实际类型: {type(client)}")


def remove_client(cfg_name: str | None = None) -> None:
    """
    移除指定配置名对应的 EmailClient。

    Args:
        cfg_name: 邮件配置名，如果不传则移除默认配置名。
    """
    # 如果未指定配置名，使用默认配置名
    if not cfg_name:
        cfg_name = DEFAULT_CFG_NAME
    # 使用锁保护全局字典的访问
    with _clients_lock:
        # 如果配置名存在，移除对应的 EmailClient 实例
        _clients.pop(cfg_name, None)


def clear() -> None:
    """
    清空所有邮件客户端。

    调用后需重新通过 set_client() 初始化客户端。
    """
    with _clients_lock:
        _clients.clear()


def send(
    cfg_name: str,
    to: str | list[str],
    subject: str,
    content: str,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    attachments: list[str] | None = None,
    is_html: bool = False
) -> EmailSendResult:
    """
    通用邮件发送方法。

    Args:
        cfg_name: 邮件配置名。
        to: 收件人邮箱地址。
        subject: 邮件主题。
        content: 邮件内容。
        cc: 抄送地址，可选。
        bcc: 密送地址，可选。
        attachments: 附件列表，可选。
        is_html: 是否为 HTML 格式，默认 False。

    Returns:
        EmailSendResult，包含发送结果信息。

    Raises:
        KeyError: 指定的配置名不存在时抛出。
    """
    return get_client(cfg_name).send(
        to, subject, content, cc=cc, bcc=bcc, attachments=attachments, is_html=is_html
    )


def send_text(
    cfg_name: str,
    to: str | list[str],
    subject: str,
    content: str,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    attachments: list[str] | None = None
) -> EmailSendResult:
    """
    快速发送纯文本邮件。

    Args:
        cfg_name: 邮件配置名。
        to: 收件人邮箱地址。
        subject: 邮件主题。
        content: 邮件内容。
        cc: 抄送地址，可选。
        bcc: 密送地址，可选。
        attachments: 附件列表，可选。

    Returns:
        EmailSendResult，包含发送结果信息。

    Raises:
        KeyError: 指定的配置名不存在时抛出。
    """
    return get_client(cfg_name).send(to, subject, content, cc=cc, bcc=bcc, attachments=attachments, is_html=False)


def send_html(
    cfg_name: str,
    to: str | list[str],
    subject: str,
    html_content: str,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    attachments: list[str] | None = None
) -> EmailSendResult:
    """
    快速发送 HTML 邮件。

    Args:
        cfg_name: 邮件配置名。
        to: 收件人邮箱地址。
        subject: 邮件主题。
        html_content: HTML 内容。
        cc: 抄送地址，可选。
        bcc: 密送地址，可选。
        attachments: 附件列表，可选。

    Returns:
        EmailSendResult，包含发送结果信息。

    Raises:
        KeyError: 指定的配置名不存在时抛出。
    """
    return get_client(cfg_name).send(to, subject, html_content, cc=cc, bcc=bcc, attachments=attachments, is_html=True)


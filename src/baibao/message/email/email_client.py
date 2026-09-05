"""
邮件客户端模块，提供邮件发送功能。

支持纯文本邮件、HTML 邮件和带附件的邮件发送，支持 SMTP/SMTPS 协议。
"""
import os
import smtplib
from dataclasses import dataclass
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from pathlib import Path
from typing import Any

from pykunlun.util import loadutil, logutil, validation

log = logutil.getLogger(__name__)


@dataclass
class EmailCfg:
    """
    邮件服务器配置类。

    封装邮件发送和接收所需的所有参数，支持多种协议。

    Attributes:
        send_server: 发送服务地址，如 ``smtp.qq.com``、``smtp.gmail.com``
        username: 发件人邮箱账号
        password: 发件人邮箱密码或授权码
        send_port: 发送服务端口，普通端口为 25，SSL 端口通常为 465
        send_protocol: 发送协议，默认 ``smtp``，加密方式（SSL/TLS）自动判断
        sender_name: 发件人显示名称，默认为邮箱账号

        receive_server: 接收服务地址，如 ``imap.qq.com``、``imap.gmail.com``
        receive_port: 接收服务端口，IMAP 通常为 993，POP3 通常为 995
        receive_protocol: 接收协议，支持 ``imap``、``pop3``，默认 ``imap``
    """
    send_server: str
    username: str
    password: str
    send_port: int = 465
    send_protocol: str = "smtp"
    sender_name: str | None = None

    receive_server: str | None = None
    receive_port: int = 993
    receive_protocol: str = "imap"

    def validate_send_config(self) -> None:
        """
        验证邮件发送配置是否有效。

        检查邮件发送连接参数的完整性。

        Raises:
            ValueError: 配置无效时抛出。
        """
        # 检查必填字段
        validation.check_required_fields_not_empty(self,
            ['send_server', 'username', 'password'], '邮件发送配置')
        # 验证端口号范围
        if not (1 <= self.send_port <= 65535):
            raise ValueError(f"发送端口号必须在 1-65535 范围内，当前值: {self.send_port}")
        # 验证发送协议（SMTP 无所谓，因为发送协议只有这个）
        if self.send_protocol.lower() != 'smtp':
            raise ValueError(f"发送协议只支持 smtp，当前值: {self.send_protocol}")

    def validate_receive_config(self) -> None:
        """
        验证邮件接收配置是否有效。

        检查邮件接收连接参数的完整性。

        Raises:
            ValueError: 配置无效时抛出。
        """
        # 检查必填字段
        validation.check_required_fields_not_empty(self,
            ['receive_server', 'username', 'password'], '邮件接收配置')
        # 验证端口号范围
        if not (1 <= self.receive_port <= 65535):
            raise ValueError(f"接收端口号必须在 1-65535 范围内，当前值: {self.receive_port}")
        # 验证接收协议（到时候看具体实现，看看是不是需要大小写敏感）
        if self.receive_protocol not in ['imap', 'pop3']:
            raise ValueError(f"接收协议只支持 imap 和 pop3，当前值: {self.receive_protocol}")

    @staticmethod
    def load_from_json_cfg(config_path: str | Path) -> 'EmailCfg':
        """
        从 JSON 文件加载邮件服务器配置。

        读取指定路径的 JSON 文件返回 EmailCfg 实例。

        Args:
            config_path: JSON 配置文件路径，支持字符串或 Path 对象。

        Returns:
            EmailCfg 实例对象。

        Raises:
            FileNotFoundError: 文件不存在时抛出。
            json.JSONDecodeError: JSON 格式解析失败时抛出。
        """
        return loadutil.load_dataclass_from_json_file(config_path, EmailCfg)


@dataclass
class EmailSendResult:
    """
    邮件发送结果。

    Attributes:
        message_id: 邮件的 Message-ID
        from_addr: 发件人地址
        recipients: 所有收件人地址列表（含抄送、密送）
        failed_recipients: 投递失败的收件人字典，空字典表示全部成功
    """
    message_id: str
    from_addr: str
    recipients: list[str]
    failed_recipients: dict[str, Any]


class EmailClient:
    """
    邮件客户端。

    支持发送纯文本邮件、HTML 邮件和带附件的邮件，基于 SMTP 协议。
    自动管理 SMTP 连接生命周期，支持连接复用。

    示例::

        from baibao.message.email import EmailClient, EmailCfg

        # 初始化
        config = EmailCfg(send_server="smtp.qq.com", send_port=465,
                          username="your_email@qq.com", password="your_auth_code")
        client = EmailClient(config)

        # 发送纯文本
        client.send(to="recipient@example.com", subject="测试", content="Hello")

        # 发送 HTML + 附件 + 抄送
        client.send(
            to=["user1@example.com", "user2@example.com"],
            subject="HTML邮件", content="<h1>Hello</h1>", is_html=True,
            attachments=["report.pdf"], cc="manager@example.com"
        )
    """

    def __init__(self, config: EmailCfg) -> None:
        """
        初始化邮件客户端。

        Args:
            config: 邮件服务器配置对象。
        """
        log.info(f"邮件客户端初始化，服务器：{config.send_server}:{config.send_port}")
        self._config = config
        self._smtp: smtplib.SMTP | None = None

    def _connect_send_server(self) -> None:
        """
        建立与 SMTP 服务器的连接并登录。

        自动判断加密方式：先尝试 SSL 直连，若失败则回退到明文 + STARTTLS。
        """
        # 检查是否已连接
        if self._smtp is not None:
            return
        # 验证发送配置
        self._config.validate_send_config()
        try:
            # 尝试 SSL 直连
            self._smtp = smtplib.SMTP_SSL(
                self._config.send_server,
                self._config.send_port,
                timeout=10
            )
        except (smtplib.SMTPException, OSError):
            # 回退到明文 + STARTTLS
            self._smtp = smtplib.SMTP(
                self._config.send_server,
                self._config.send_port,
                timeout=10
            )
            # 启用 TLS 加密
            self._smtp.starttls()
        # 登录 SMTP 服务器
        self._smtp.login(self._config.username, self._config.password)

    def _disconnect_send_server(self) -> None:
        """
        关闭与 SMTP 服务器的连接。
        """
        # 检查是否已连接
        if self._smtp:
            try:
                # 关闭 SMTP 连接
                self._smtp.quit()
            except Exception:
                pass
            # 重置 SMTP 连接
            self._smtp = None

    def _create_message(
        self,
        to: str | list[str],
        subject: str,
        content: str,
        content_type: str = "plain",
        attachments: list[str] | None = None
    ) -> MIMEMultipart:
        """
        创建邮件消息对象。

        Args:
            to: 收件人邮箱地址，单个字符串或列表。
            subject: 邮件主题。
            content: 邮件内容。
            content_type: 内容类型，``plain`` 或 ``html``。
            attachments: 附件文件路径列表。

        Returns:
            组装好的 MIMEMultipart 消息对象。
        """
        # 创建 MIMEMultipart 消息对象
        msg = MIMEMultipart()
        msg['Message-ID'] = make_msgid()
        # 设置消息头
        sender_name = self._config.sender_name or self._config.username
        msg['From'] = f"{Header(sender_name, 'utf-8')} <{self._config.username}>"
        msg['To'] = ", ".join(to) if isinstance(to, list) else to
        msg['Subject'] = str(Header(subject, 'utf-8'))
        # 添加内容部分
        if content_type == "html":
            part = MIMEText(content, 'html', 'utf-8')
        else:
            part = MIMEText(content, 'plain', 'utf-8')
        msg.attach(part)
        # 添加附件部分
        if attachments:
            for file_path in attachments:
                # 检查文件是否存在
                if not os.path.exists(file_path):
                    raise FileNotFoundError(f"附件文件不存在: {file_path}")
                # 读取文件内容
                with open(file_path, 'rb') as f:
                    # 创建 MIMEApplication 部分
                    attachment_part = MIMEApplication(f.read())
                    attachment_part.add_header(
                        'Content-Disposition',
                        'attachment',
                        filename=('utf-8', '', os.path.basename(file_path))
                    )
                    # 添加附件部分到消息
                    msg.attach(attachment_part)
        # 返回组装好的 MIMEMultipart 消息对象
        return msg

    def send(
        self,
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
            to: 收件人邮箱地址，单个字符串或列表。
            subject: 邮件主题。
            content: 邮件正文内容。
            cc: 抄送地址，单个字符串或列表，可选。
            bcc: 密送地址，单个字符串或列表，可选。
            attachments: 附件文件路径列表，可选。
            is_html: 内容是否为 HTML 格式，默认 False。

        Returns:
            EmailSendResult，包含 message_id、收件人列表、失败收件人等信息。
        """
        try:
            # 连接到 SMTP 服务器
            self._connect_send_server()
            # 创建邮件消息对象
            content_type = "html" if is_html else "plain"
            msg = self._create_message(to, subject, content, content_type, attachments)
            # 添加抄送和密送地址
            if cc:
                msg['Cc'] = ", ".join(cc) if isinstance(cc, list) else cc
            if bcc:
                msg['Bcc'] = ", ".join(bcc) if isinstance(bcc, list) else bcc
            # 合并所有收件人地址
            all_recipients: list[str] = []
            all_recipients.extend(to if isinstance(to, list) else [to])
            if cc:
                all_recipients.extend(cc if isinstance(cc, list) else [cc])
            if bcc:
                all_recipients.extend(bcc if isinstance(bcc, list) else [bcc])
            # 发送邮件消息
            assert self._smtp is not None
            failed_recipients = self._smtp.sendmail(
                self._config.username, all_recipients, msg.as_string()
            )
            log.info(f"邮件发送成功，收件人：{all_recipients}")
            # 返回发送结果
            return EmailSendResult(
                message_id=msg['Message-ID'],
                from_addr=self._config.username,
                recipients=all_recipients,
                failed_recipients=failed_recipients,
            )
        finally:
            # 断开 SMTP 服务器连接
            self._disconnect_send_server()

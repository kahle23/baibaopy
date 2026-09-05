# 消息发送模块

提供统一的消息发送接口，支持多种消息渠道。当前已实现邮件发送功能，未来可扩展支持更多消息渠道（如短信、即时通讯等）。

邮件功能支持纯文本、HTML 和带附件的邮件发送，支持抄送、密送和群发。自动判断 SSL / STARTTLS 加密方式。

---

## 1. 模块结构

| 子模块 | 说明 |
|--------|------|
| `baibao.message.email` | 邮件发送子模块 |
| `baibao.message` | 统一入口，导出邮件相关类和函数 |

---

## 2. 邮件发送功能

### 2.1 核心类与函数

| 名称 | 类型 | 说明 |
|------|------|------|
| `EmailCfg` | dataclass | 邮件服务器配置类 |
| `EmailSendResult` | dataclass | 邮件发送结果 |
| `EmailClient` | class | 邮件客户端，负责连接和发送 |
| `set_client()` | function | 注册邮件配置（支持多配置管理） |
| `get_client()` | function | 获取邮件客户端实例 |
| `remove_client()` | function | 移除指定配置的客户端 |
| `clear()` | function | 清空所有客户端 |
| `send()` | function | 通用邮件发送 |
| `send_text()` | function | 发送纯文本邮件 |
| `send_html()` | function | 发送 HTML 邮件 |

### 2.2 配置（EmailCfg）

#### 属性

| 属性 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `send_server` | str | 是 | - | 发送服务器地址，如 `smtp.qq.com` |
| `username` | str | 是 | - | 发件人邮箱账号 |
| `password` | str | 是 | - | 发件人邮箱密码或授权码 |
| `send_port` | int | 否 | 465 | 发送端口 |
| `send_protocol` | str | 否 | `"smtp"` | 发送协议 |
| `sender_name` | str \| None | 否 | - | 发件人显示名称 |
| `receive_server` | str \| None | 否 | - | 接收服务器地址（预留） |
| `receive_port` | int | 否 | 993 | 接收端口 |
| `receive_protocol` | str | 否 | `"imap"` | 接收协议 |

#### 创建配置

```python
from baibao.message.email import EmailCfg

# 方式一：直接传入参数
config = EmailCfg(
    send_server="smtp.qq.com",
    send_port=465,
    username="your_email@qq.com",
    password="your_auth_code",
    sender_name="我的邮箱"
)

# 方式二：从 JSON 文件加载
config = EmailCfg.load_from_json_cfg("email_config.json")
```

#### 验证配置

```python
# 验证发送配置
config.validate_send_config()

# 验证接收配置
config.validate_receive_config()
```

### 2.3 使用 EmailClient 直接发送

#### 发送纯文本邮件

```python
from baibao.message.email import EmailClient, EmailCfg

config = EmailCfg(
    send_server="smtp.qq.com",
    send_port=465,
    username="your_email@qq.com",
    password="your_auth_code"
)

client = EmailClient(config)
result = client.send(
    to="recipient@example.com",
    subject="测试邮件",
    content="这是一封测试邮件"
)
print(result.message_id)
print(result.failed_recipients)  # 空字典表示全部成功
```

#### 发送 HTML 邮件

```python
html = """
<html>
<body>
    <h1>欢迎</h1>
    <p>这是一封 <strong>HTML</strong> 格式的邮件。</p>
</body>
</html>
"""

client.send(
    to="recipient@example.com",
    subject="HTML 邮件",
    content=html,
    is_html=True
)
```

#### 发送带附件的邮件

```python
client.send(
    to="recipient@example.com",
    subject="带附件的邮件",
    content="请查看附件",
    attachments=["report.pdf", "data.xlsx"]
)
```

#### 群发（含抄送、密送）

```python
client.send(
    to=["user1@example.com", "user2@example.com"],
    subject="群发邮件",
    content="Hello Everyone!",
    cc="manager@example.com",
    bcc=["admin1@example.com", "admin2@example.com"]
)
```

#### send() 方法参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `to` | str \| list | 是 | 收件人邮箱地址 |
| `subject` | str | 是 | 邮件主题 |
| `content` | str | 是 | 邮件正文内容 |
| `cc` | str \| list \| None | 否 | 抄送地址 |
| `bcc` | str \| list \| None | 否 | 密送地址 |
| `attachments` | list \| None | 否 | 附件文件路径列表 |
| `is_html` | bool | 否 | 内容是否为 HTML 格式，默认 `False` |

### 2.4 使用快捷函数（多配置管理）

适合需要管理多个邮件服务器配置的场景。

#### 注册配置

```python
from baibao.message import email

# 方式一：使用字典
email.set_client("qq", {
    "send_server": "smtp.qq.com",
    "send_port": 465,
    "username": "your_email@qq.com",
    "password": "your_auth_code",
    "sender_name": "我的邮箱"
})

# 方式二：使用 EmailCfg 对象
from baibao.message.email import EmailCfg
config = EmailCfg(...)
email.set_client("work", config)

# 方式三：直接使用 EmailClient 实例
from baibao.message.email import EmailClient
client = EmailClient(config)
email.set_client("backup", client)
```

#### 发送纯文本邮件

```python
# 使用默认配置名 "default"
email.send_text("default", to="user@example.com", subject="测试", content="内容")

# 使用指定配置名
email.send_text("qq", to="user@example.com", subject="测试", content="内容")
```

#### 发送 HTML 邮件

```python
email.send_html("qq", to="user@example.com", subject="欢迎",
                html_content="<h1>Hello</h1>")
```

#### 通用发送

```python
result = email.send(
    cfg_name="qq",
    to="user@example.com",
    subject="测试",
    content="内容",
    cc="manager@example.com",
    bcc=["admin@example.com"],
    attachments=["report.pdf"],
    is_html=False
)
```

#### 其他管理函数

```python
# 获取客户端实例
client = email.get_client("qq")

# 移除指定配置
email.remove_client("qq")

# 清空所有配置
email.clear()
```

### 2.5 发送结果（EmailSendResult）

```python
from baibao.message.email import EmailSendResult

result = client.send(to="user@example.com", subject="测试", content="内容")

# 属性
result.message_id         # 邮件 Message-ID
result.from_addr          # 发件人地址
result.recipients         # 所有收件人列表（含抄送、密送）
result.failed_recipients  # 投递失败的收件人字典，空字典表示全部成功
```

---

## 3. 注意事项

- `password` 字段通常需要使用邮箱的**授权码**，而非登录密码
- 端口 465 使用 SSL 加密，端口 25 使用 STARTTLS 加密
- `EmailClient` 会自动管理 SMTP 连接的生命周期，无需手动连接/断开
- 快捷函数 `email.set_client()` 支持线程安全的配置管理
- 附件路径必须是文件系统中的有效路径
# BaiBao (百宝)

方便好用的 Python 常用功能库。把日常开发中反复用到的能力（日志、包管理、数据库、邮件、OCR、自动化测试）封装成简洁的 API，开箱即用。<br />

[![PyPI](https://img.shields.io/pypi/v/baibao.svg)](https://pypi.org/project/baibao/)

<br />

## 优势

- **统一抽象，随时替换** — LLM、OCR、模板引擎均采用策略模式设计，共享同一抽象接口，运行时自由切换后端实现
- **零配置依赖管理** — `import_module()` 首次导入时自动检测并安装缺失的包；pip 操作内置清华、阿里云等多镜像自动 fallback
- **数据库双模支持** — 连接池模式（高并发/线程安全）与单连接模式（轻量级）自由切换，MySQL / PostgreSQL 驱动自动安装
- **可扩展 CLI 框架** — 基于注册机制的命令行系统，支持命令缩写、帮助命令、线程安全，轻松添加自定义命令
- **配置即代码** — 所有配置类均为 `dataclass`，支持从 JSON 一键加载，自动校验必填字段，拒绝运行时 TypeError
- **邮件收发一体** — 支持纯文本、HTML 和附件，自动判断 SSL / STARTTLS 加密方式，抄送/密送/群发一应俱全

<br />

## 开始使用

### 安装

```bash
# 安装 baibao（使用清华镜像源加速）
pip install baibao -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

<br />

### OCR 引擎（可选）

`baibao.ai.ocr` 默认用 EasyOCR，也可切 PaddleOCR。PaddleOCR 封装会**按已装版本自动选择** `PaddleOcrV2`（2.x API）或 `PaddleOcrV3`（3.x API），无需钉版本。两者依赖较重，按需装其一：

```bash
# EasyOCR（默认，多语言）—— baibao 命令默认引擎
python -m pip install easyocr opencv-python numpy -i https://pypi.tuna.tsinghua.edu.cn/simple/

# PaddleOCR（中文精度更高）—— 2.x / 3.x 均可，baibao 自动适配
python -m pip install paddlepaddle paddleocr opencv-python numpy -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

> PaddleOCR 在新版 Python（3.13）/ Windows 上有几个坑（torch 的 `c10.dll`、paddlepaddle 的 mkldnn PIR bug、3.0.0 与 PP-OCRv6 不兼容等），其中 mkldnn 的坑 baibao 已内置绕过。详见 [AI 模块文档 — PaddleOCR 版本与已知坑](./docs/ai/ai.md)。

<br />

### 模块概览

> 完整文档清单与使用指南见 [docs/README.md](./docs/README.md)。

| 分类 | 模块 | 功能 | 文档 |
|------|------|------|------|
| **AI** | `baibao.ai` | LLM 对话（OpenAI 兼容）、OCR 文字识别（EasyOCR/PaddleOCR） | [文档](https://github.com/kahle23/pybaibao/blob/master/docs/ai/ai.md) |
| **AI Agent** | `baibao.ai_agent` | AI 记忆（`am`）、计划任务（`pt`）、prompt 模板库（`ap`） | [记忆](https://github.com/kahle23/pybaibao/blob/master/docs/ai/agent_memory.md) · [计划任务](https://github.com/kahle23/pybaibao/blob/master/docs/ai/plan_task.md) · [prompt 模板](https://github.com/kahle23/pybaibao/blob/master/docs/ai/agent_prompt.md) |
| **数据** | `baibao.data` | 货币查询、元数据定义（Field/Style） | [文档](https://github.com/kahle23/pybaibao/blob/master/docs/data/data.md) |
| **渲染** | `baibao.render` | HTML 报告片段（表格/图表/指标卡片）、模板引擎（Jinja2） | [文档](https://github.com/kahle23/pybaibao/blob/master/docs/output/render.md) |
| **数据库** | `baibao.db` | MySQL/PostgreSQL 连接池与查询 | [文档](https://github.com/kahle23/pybaibao/blob/master/docs/data/db.md) |
| **消息** | `baibao.message` | 邮件发送（文本/HTML/附件） | [文档](https://github.com/kahle23/pybaibao/blob/master/docs/output/message.md) |
| **命令行** | `baibao.cli` | CLI 命令行工具（包管理、项目清理、数据库、OCR 等） | [文档](https://github.com/kahle23/pybaibao/blob/master/docs/cli.md) |
| **自动化测试** | `baibao.autotest` | Playwright E2E 测试基础设施（BasePage/真实输入/登录态缓存/DOM 探针） | [文档](https://github.com/kahle23/pybaibao/blob/master/docs/test/autotest.md) |

<br />

## 开发指南

### 本地安装

```bash
# 开发模式（修改源码立刻生效，无需重新安装）
python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 基础安装
python -m pip install . -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

<br />

### 开发依赖

```bash
# 安装开发依赖（代码检查、测试等）
python -m pip install ".[dev]"

# 安装全部依赖
python -m pip install ".[all]"



python -m pip install --upgrade baibao -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

<br />

### 代码质量

```bash
# 代码风格检查
python -m ruff check src/

# 类型检查
python -m mypy src/

# 类型检查（Pyright strict，配置在 pyrightconfig.json；
# 全局安装：npm install -g pyright，国内镜像加 --registry=https://registry.npmmirror.com）
pyright src/

# 运行测试
python -m pytest tests/
```

<br />

### 打包上传

```bash
# 清理 __pycache__ 缓存
python -m baibao py_clean .

# 构建源码包和 wheel 包
python -m build

# 构建结果
ls dist/

# 检查元数据
python -m twine check dist/*

# 上传到正式 PyPI
python -m twine upload dist/*

```

<br />

## 许可证

GPL-3.0-or-later

<br />

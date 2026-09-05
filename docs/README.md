# BaiBao 使用指南

百宝（baibao）—— 方便好用的 Python 常用功能库，把日常开发反复用到的能力（AI、数据库、邮件、渲染、自动化测试、CLI 工具）封装成简洁 API，开箱即用。安装总入口见 [根 README](../README.md#开始使用)。

## 从哪开始

- **第一次用**：看 [根 README](../README.md) 的"开始使用"，3 步装好并跑通。
- **要用某个模块**：直接开下表对应篇，篇内"快速上手"在最前。
- **遇到报错 / 升级版本**：跳各篇的"已知坑与版本兼容"小节，坑表一行一坑方便定位。

## 文档清单

| 文档 | 类型 | 一句话定位 |
|------|------|-----------|
| [cli.md](./cli.md) | 命令 | `python -m baibao` 全部命令行工具速查与详解 |
| [ai/ai.md](./ai/ai.md) | 模块 | LLM 对话（OpenAI 兼容）与 OCR 文字识别 |
| [ai/agent_memory.md](./ai/agent_memory.md) | 命令 | AI 记忆：记/回忆/更新/遗忘事实类项目知识（`am`） |
| [ai/agent_prompt.md](./ai/agent_prompt.md) | 命令 | AI prompt 模板库：团队共享/可搜索/可参数化（`ap`） |
| [ai/plan_task.md](./ai/plan_task.md) | 命令 | AI 计划任务：建任务/拆步骤/认领/断点续跑（`pt`） |
| [data/data.md](./data/data.md) | 模块 | 货币查询与元数据定义（Field/Style） |
| [data/db.md](./data/db.md) | 模块 | MySQL/PostgreSQL/SQLite 连接池与查询 |
| [output/render.md](./output/render.md) | 模块 | HTML 报告片段（表格/图表/指标卡片）与 Jinja2 模板引擎 |
| [output/message.md](./output/message.md) | 模块 | 邮件发送（文本/HTML/附件，自动判 SSL/STARTTLS） |
| [test/autotest.md](./test/autotest.md) | 模块 | Playwright E2E 测试基础设施 |

## 可选能力与依赖

baibao 核心依赖很轻（仅 `pykunlun`），重依赖按需安装：

| 能力 | 安装方式 | 文档 |
|------|---------|------|
| OCR — EasyOCR（默认引擎） | `pip install easyocr opencv-python numpy` | [ai.md](./ai/ai.md) |
| OCR — PaddleOCR（中文精度更高） | `pip install paddlepaddle paddleocr opencv-python numpy` | [ai.md](./ai/ai.md) |
| OCR HTTP 常驻服务 | `pip install bottle`（首次启动自动安装） | [cli.md](./cli.md#ocr_server--ocr-http-服务) |
| 自动化测试 | `pip install "baibao[autotest]"` | [autotest.md](./test/autotest.md) |
| 全部可选依赖 | `pip install "baibao[all]"` | — |
| 开发依赖（ruff/mypy/pytest） | `pip install "baibao[dev]"` | — |

> 国内镜像加速：上述命令末尾加 `-i https://pypi.tuna.tsinghua.edu.cn/simple/`。`pip_install` / `pip_upgrade` 命令已内置清华/阿里云/官方源自动 fallback。

## CLI 命令一览

完整命令清单与逐命令详解见 [cli.md](./cli.md)，此处仅作速查：

| 命令（缩写） | 功能 |
|------|------|
| `help` | 显示帮助信息 |
| `pip_install` | 安装 Python 包（自动切换镜像） |
| `pip_upgrade` | 升级 Python 包 |
| `py_clean` | 清理 build/dist/egg-info/__pycache__ |
| `python_path_setup` | 把 Python 安装目录与 Scripts 加到 PATH |
| `kbase_init` | 生成知识库目录骨架 / 新增项目目录 |
| `rdb`（`r`） | 数据库查询/执行/列出库 |
| `rdb_dump`（`rd`） | 数据库备份转储 |
| `ocr` | 识别图片文字（easy/paddle 引擎） |
| `ocr_server`（`ocs`） | 启动常驻内存 OCR HTTP 服务 |
| `agent_image` | 从 AI agent 会话库取最新粘贴图并解码为本地文件 |
| `agent_memory`（`am`） | AI 记忆操作（记/回忆/更新/遗忘） |
| `agent_prompt`（`ap`） | AI prompt 模板库 |
| `plan_task`（`pt`） | AI 计划任务（建任务/拆步骤/认领/续跑） |
| `mojibake` | 检测/修复源文件乱码（UTF-8 当 GBK 解读型） |
| `move_java` | 迁移 Java 源文件到新包（改 package + 同步 import） |

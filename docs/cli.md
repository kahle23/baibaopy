# cli — 命令行工具

> 基于 `python -m baibao <命令>` 调用的全部命令行工具。核心依赖很轻，重依赖（OCR 引擎、bottle 等）按需安装，详见 [可选能力与依赖](./README.md#可选能力与依赖)。

## 命令清单

| 命令（缩写） | 功能 | 用法一行 |
|------|------|------|
| [`help`](#help) | 显示帮助信息 | `python -m baibao help [命令名]` |
| [`pip_install`](#pip_install) | 安装 Python 包（自动切换镜像） | `python -m baibao pip_install <包名> [包名2 ...]` |
| [`pip_upgrade`](#pip_upgrade) | 升级 Python 包 | `python -m baibao pip_upgrade <包名> [包名2 ...]` |
| [`py_clean`](#py_clean) | 清理构建缓存 | `python -m baibao py_clean [目录路径]` |
| [`python_path_setup`](#python_path_setup) | 把 Python 目录与 Scripts 加到 PATH | `python -m baibao python_path_setup [--force]` |
| [`kbase_init`](#kbase_init) | 生成知识库目录骨架 / 新增项目目录 | `python -m baibao kbase_init -t <模板> [目录]` |
| [`rdb`](#rdb)（`r`） | 数据库查询/执行/列出库 | `python -m baibao rdb <list\|query\|execute> [选项]` |
| [`rdb_dump`](#rdb_dump)（`rd`） | 数据库备份转储 | `python -m baibao rdb_dump [选项]` |
| [`ocr`](#ocr) | 识别图片文字 | `python -m baibao ocr <图片路径> [选项]` |
| [`ocr_server`](#ocr_server)（`ocs`） | 启动常驻内存 OCR HTTP 服务 | `python -m baibao ocr_server [选项]` |
| [`agent_image`](#agent_image) | 从 AI agent 会话库取最新粘贴图 | `python -m baibao agent_image [选项]` |
| [`agent_memory`](#agent_memory)（`am`） | AI 记忆操作（记/回忆/更新/遗忘） | `python -m baibao am <子命令> [选项]` |
| [`agent_prompt`](#agent_prompt)（`ap`） | AI prompt 模板库 | `python -m baibao ap <子命令> [选项]` |
| [`plan_task`](#plan_task)（`pt`） | AI 计划任务（建任务/拆步骤/认领/续跑） | `python -m baibao pt <子命令> [选项]` |
| [`mojibake`](#mojibake) | 检测/修复源文件乱码 | `python -m baibao mojibake [路径] [选项]` |
| [`move_java`](#move_java) | 迁移 Java 源文件到新包 | `python -m baibao move_java <src> <dest> [选项]` |
| [`autotest`](#autotest)（`au`） | Web 自动化测试辅助（DOM 摘要探针） | `python -m baibao autotest probe <路由> [选项]` |

> `agent_memory` / `agent_prompt` / `plan_task` 子命令丰富、概念独立，本篇只给命令行调用差异，完整语义见各自的命令篇：[agent_memory.md](./ai/agent_memory.md)、[agent_prompt.md](./ai/agent_prompt.md)、[plan_task.md](./ai/plan_task.md)。`rdb` 的 SQL API 语义见 [db.md](./data/db.md)，`ocr` 的引擎参数语义见 [ai.md](./ai/ai.md)。

---

## help

### 用法

```bash
python -m baibao help [命令名]
```

### 示例

```bash
# 查看所有命令
python -m baibao help

# 查看特定命令的详细用法
python -m baibao help pip_install
```

---

## pip_install

安装 Python 包，内置清华、阿里云、官方 PyPI 多镜像自动 fallback：某源失败自动切下一个，无需手动配置。

### 用法

```bash
python -m baibao pip_install <包名> [包名2 ...]
```

### 示例

```bash
# 安装单个包
python -m baibao pip_install requests

# 安装多个包
python -m baibao pip_install numpy pandas matplotlib
```

---

## pip_upgrade

升级 Python 包，镜像源策略同 `pip_install`。

### 用法

```bash
python -m baibao pip_upgrade <包名> [包名2 ...]
```

### 示例

```bash
python -m baibao pip_upgrade requests
python -m baibao pip_upgrade numpy pandas
```

---

## py_clean

清理构建缓存目录。递归删除以下内容：`build/`、`dist/`、`*.egg-info`、`__pycache__/`。

### 用法

```bash
python -m baibao py_clean [目录路径]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `目录路径` | 路径 | 否 | 当前目录 | 要清理的项目根目录 |

### 示例

```bash
python -m baibao py_clean            # 清理当前目录
python -m baibao py_clean /path/to/project
```

---

## python_path_setup

将 Python 安装目录与 Scripts 目录加入 PATH 环境变量。创建 `PYTHON_HOME` 与 `PYTHON_SCRIPT_HOME` 环境变量后引用加入 PATH，跨平台（Windows 用 set，类 Unix 用 export 提示）。

### 用法

```bash
python -m baibao python_path_setup [--force]
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--force` | 开关 | 否 | 关 | 已存在但值不同时直接覆盖，不再交互确认 |

### 输出

- 检查与设置过程走日志输出；完成后提示重启终端或执行给出的 `set`/`export` 命令使变量生效。
- Scripts 目录不存在时会交互询问是否创建。

---

## kbase_init

通过「模板」定义知识库长什么样，用 `-t/--template` 指定模板（必填）。内置「公司」模板：公司资料、项目资产、运营支持、团队流程、知识沉淀、资源工具、归档等顶层职能域，预置「自研项目」「三方系统」两套可复制样板。

### 用法

```bash
python -m baibao kbase_init -t <模板> [目录]          # 生成完整骨架（默认当前目录）
python -m baibao kbase_init list                      # 列出所有可用模板
python -m baibao kbase_init new -t <模板> <类型> <标题> [目录]   # 新增一个项目目录
```

| 选项/参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `-t, --template` | 模板名 | 是 | — | 知识库模板，如「公司」 |
| `目录` | 路径 | 否 | 当前目录 | 知识库根目录 |
| `类型`（new 子命令） | 模板定义的项目类型 | 是 | — | 如「自研」「三方」 |
| `标题`（new 子命令） | str | 是 | — | 项目名 |

### 示例

```bash
# 生成完整骨架（必须指定模板，默认当前目录）
python -m baibao kbase_init -t 公司

# 列出所有可用模板
python -m baibao kbase_init list

# 新增自研项目（编号自动取下一个，如 01-xxx）
python -m baibao kbase_init new -t 公司 自研 我的系统

# 新增三方系统（指定目录 + 模板）
python -m baibao kbase_init new 三方 采购系统 /path/to/knowledge-base -t 公司
```

设计原则：顶层按稳定的职能域划分，文件用命名规范承载维度（日期/项目/类型/标题/版本），不靠多层文件夹表达维度；样板项目（以"模板"开头）固定使用 99 编号。

### 扩展：新增知识库模板

模板是 `KbaseTemplate` 的实例，注册后立即可用，命令侧无需改动：

```python
from baibao.cli.kbase_command import KbaseTemplate, register_template

register_template(KbaseTemplate(
    name="个人",                  # 用作 -t 参数
    description="个人知识库",
    top_levels={"00-首页": "...", "01-笔记": "..."},
    second_levels={"00-首页": [], "01-笔记": ["技术", "生活"]},
    project_templates={"个人项目": {"00-概览": [], "01-笔记": []}},
    seed_projects=[("个人项目", "模板个人项目")],
))
```

注册后即可 `python -m baibao kbase_init -t 个人`。同分类多版本只需注册多个不同 `name`（如「公司」「公司v2」）。

---

## rdb

数据库操作命令，基于 `baibao.db.rdb` 的 `rdb_mgr`。子命令：`list`（列出已注册库）、`query`（查询）、`execute`（写操作）。SQL API 语义、配置文件、连接池/单连接模式见 [db.md](./data/db.md)。

### 用法

```bash
python -m baibao rdb <子命令> [选项]
```

| 子命令 | 功能 |
|------|------|
| `list` | 列出已注册的数据库名称 |
| `query` | 执行 SQL 查询并显示结果 |
| `execute` | 执行 SQL 语句（INSERT/UPDATE/DELETE/DDL） |

### query 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--sql SQL` | str | 与 `--sql-file` 二选一 | — | 内联 SQL；含双引号时易被 shell argv 剥离，改用 `--sql-file` |
| `--sql-file PATH` | 路径 | 与 `--sql` 二选一 | — | 从 UTF-8 文件读 SQL；传 `-` 读 stdin，适合含引号/特殊字符/超长 SQL |
| `--db NAME` | str | 否 | `default` | 数据库实例名 |
| `--format FMT` | 枚举 | 否 | `jsonl` | 输出格式：`json` / `jsonl` / `csv` / `table` |

### execute 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--sql SQL` | str | 与 `--sql-file` 二选一 | — | 内联 SQL |
| `--sql-file PATH` | 路径 | 与 `--sql` 二选一 | — | 从文件/stdin 读 SQL |
| `--db NAME` | str | 否 | `default` | 数据库实例名 |

### 示例

```bash
# 列出已注册数据库
python -m baibao rdb list

# 内联 SQL 查询（默认 jsonl 输出）
python -m baibao rdb query --sql "SELECT id, name FROM users LIMIT 5"

# 含双引号/特殊字符的 SQL 走文件
python -m baibao rdb query --sql-file query.sql --db mydb --format table

# 从 stdin 读 SQL
echo "SELECT 1" | python -m baibao rdb query --sql-file -

# 写操作
python -m baibao rdb execute --sql "UPDATE users SET active=1 WHERE id=10"
```

### 输出

- 查询结果走 **stdout**，日志走 **stderr**；`--format jsonl` 每行一个 JSON 对象，`table` 带对齐表头与"共 N 条记录"。
- `execute` 成功时日志输出受影响行数；失败返回非零退出码。

---

## rdb_dump

数据库备份（转储）。通过适配器工厂按数据库类型执行备份，支持 MySQL/PostgreSQL/SQLite（实际可用类型取决于已注册的备份适配器）。

### 用法

```bash
python -m baibao rdb_dump [选项]
```

### 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `-c, --config NAME` | str | 否 | `default` | 数据库配置名 |
| `-f, --config-file PATH` | 路径 | 否 | `./db.config` | 数据库配置文件路径 |
| `-o, --output DIR` | 路径 | 否 | `./backups` | 备份输出目录 |
| `--tables TABLES` | str | 否 | — | 只备份指定的表，逗号分隔 |
| `--schema-only` | 开关 | 否 | 关 | 只备份结构，不备份数据 |
| `--no-compress` | 开关 | 否 | 关 | 不压缩备份文件（默认压缩为 `.sql.gz`） |
| `--verbose` | 开关 | 否 | 关 | 显示详细输出 |

### 示例

```bash
# 默认配置 default + ./db.config，输出到 ./backups
python -m baibao rdb_dump

# 指定配置名 + 输出目录
python -m baibao rdb_dump -c prod -o /backup/db

# 只备份指定表，不压缩
python -m baibao rdb_dump --tables users,orders --no-compress

# 只备份结构
python -m baibao rdb_dump --schema-only
```

### 输出

- 备份成功日志输出 `[OK] ...`，失败输出 `[FAIL] ...`；备份文件名按 `db_type-database-时间戳.sql[.gz]` 命名。

---

## ocr

识别图片中的文字，结果输出到 stdout。默认 EasyOCR，可切 PaddleOCR（按已装版本自动选 V2/V3）。引擎参数差异、版本兼容与已知坑见 [ai.md](./ai/ai.md)。

### 用法

```bash
python -m baibao ocr <图片路径> [选项]
```

### 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `<图片路径>` | 路径 | 是 | — | 待识别图片 |
| `-e, --engine` | 枚举 | 否 | `easy` | 引擎：`easy` / `paddle`（按已装版本自动选 V2/V3）/ `paddle2` / `paddle3` |
| `--lang CODE` | str | 否 | `ch` | 识别语言：`ch`（中英）/ `en` / `japan` / `ko` / `ch_tra` |
| `--gpu` | 开关 | 否 | 关 | 启用 GPU（easyocr 需 CUDA 版 torch；paddle 需 `paddlepaddle-gpu`） |
| `--cpu-threads N` | int | 否 | — | CPU 推理线程数（仅 paddle 生效）。建议取核数一半以上 |
| `-d, --details` | 开关 | 否 | 关 | 输出 JSON 数组，每项一行紧凑输出：`text` / `confidence`(0~1) / `bbox` |
| `--delim STR` | str | 否 | — | 结果分隔符：设则在结果前后各占一行包裹，便于精准截取 |
| `--draw PATH` | 路径 | 否 | — | 将识别框绘制到图片并保存到 PATH |

### 示例

```bash
# 默认 easy 引擎
python -m baibao ocr image.png

# PaddleOCR（中文精度更高，自动按已装版本选 V2/V3）
python -m baibao ocr image.png --engine paddle

# 指定语言 + 多线程 CPU
python -m baibao ocr image.png --engine paddle --lang en --cpu-threads 8

# 启用 GPU
python -m baibao ocr image.png --engine paddle --gpu

# 强制指定版本
python -m baibao ocr image.png --engine paddle3   # 3.x API

# 输出含坐标与置信度的 JSON 详情
python -m baibao ocr image.png --engine paddle --details

# 用分隔符包裹结果（防日志混入 stdout，配合 2>$null 最稳）
python -m baibao ocr image.png --engine paddle --delim __OCR__ 2>$null

# 把识别框画到图上另存
python -m baibao ocr image.png --engine paddle --draw out.png
```

### 输出

- 识别文字走 **stdout**，日志/下载进度走 **stderr**，两路分离；Windows 下 stdout 已强制 UTF-8。
- 想要干净输出（只留文字），命令末尾加 `2>$null` 丢弃 stderr；担心 OCR 框架偶发把日志写到 stdout 时，加 `--delim <STR>` 包裹结果，取两个分隔符行之间的内容。
- 失败时 stdout 输出 `(OCR 失败：…)` 标记；首次运行会自动下载引擎依赖与模型（一次性）。
- PaddleOCR 在 Python 3.13 / Windows 上的版本兼容与已知坑见 [AI 模块文档](./ai/ai.md#paddleocr-版本与已知坑重要)。

---

## ocr_server — OCR HTTP 服务

启动常驻内存的 OCR HTTP 服务：模型在启动时按 `--engines` 预加载一次并驻留内存，避免每次 OCR 都重新加载模型。请求时通过 `engine` 参数选择已加载的引擎。引擎参数语义见 [ai.md](./ai/ai.md)。

### 用法

```bash
python -m baibao ocr_server [选项]    # Ctrl+C 退出
```

### 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--host HOST` | str | 否 | `127.0.0.1` | 监听地址 |
| `-p, --port PORT` | int | 否 | `8000` | 监听端口；传 `0` 由系统分配空闲端口 |
| `--engines LIST` | str | 否 | `easy` | 预加载引擎，逗号分隔（`easy` / `paddle` / `paddle2` / `paddle3`） |
| `--lang CODE` | str | 否 | `ch` | 识别语言，作用于所有预加载引擎 |
| `--gpu` | 开关 | 否 | 关 | 启用 GPU |
| `--cpu-threads N` | int | 否 | — | CPU 推理线程数（仅 paddle 引擎生效） |
| `--no-angle-cls` | 开关 | 否 | 关 | 关闭角度/方向分类（更快，但倾斜文本识别变差） |
| `--default-engine NAME` | str | 否 | `--engines` 第一个 | 请求未指定 engine 时使用的默认引擎 |
| `--max-image-mb N` | int | 否 | `16` | 单次请求图片大小上限（MB） |

### 示例

```bash
# 默认加载 easy
python -m baibao ocr_server

# 同时加载两个引擎
python -m baibao ocr_server --engines easy,paddle3

# PaddleOCR + 英文 + GPU + 自定义端口
python -m baibao ocr_server --engines paddle --lang en --gpu --port 9000
```

### 输出

服务启动后阻塞运行直到 Ctrl+C。调用方式（服务启动后）：

```bash
# 1) multipart 上传文件（最常用）
curl -F "image=@shot.png" -F "engine=paddle" http://127.0.0.1:8000/ocr

# 2) JSON base64（适合跨机器调用）
curl -H "Content-Type: application/json" \
     -d "{\"image_base64\":\"$(base64 -w0 shot.png)\",\"details\":true}" \
     http://127.0.0.1:8000/ocr

# 3) JSON 本地路径（仅适合服务所在机器，省去上传开销）
curl -H "Content-Type: application/json" \
     -d "{\"image_path\":\"C:/shots/shot.png\"}" http://127.0.0.1:8000/ocr
```

- 首次启动若未装 `bottle` 会自动安装（与 OCR 引擎的自动安装策略一致）。
- 未在启动时加载的引擎会被拒绝，从而尊重"哪些模型加载、哪些不加载"的配置意图。

---

## agent_image

从 AI agent（如 opencode）的会话库取"最新一张粘贴图"。很多 AI agent 把用户粘贴的图片以 base64 内联存入会话存储（文件名常被丢弃），模型不支持视觉输入时无法直接看到。本命令按 `--agent` 选择适配器，取最新一条图片记录，base64 解码落地为临时图片文件，向 stdout 输出其绝对路径，供下游（OCR、文档生成等）继续处理。

### 用法

```bash
python -m baibao agent_image [选项]
```

### 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--agent NAME` | 枚举 | 否 | `opencode` | 来源 agent（当前支持 `opencode`） |
| `--max-age N` | int | 否 | `120` | 只取该秒数内的图片；`0` 不限时效，取库里最新 |
| `--db PATH` | 路径 | 否 | 按 `--agent` 推断 | 会话库路径 |
| `--out-dir PATH` | 路径 | 否 | 系统临时目录 | 解码后临时图存放目录 |
| `--list` | 开关 | 否 | 关 | 仅列出最近 5 条图片记录元信息，不解码 |
| `--delim STR` | str | 否 | — | 结果分隔符：设则在 stdout 结果前后各占一行包裹，便于精准截取 |

### 示例

```bash
# 默认 agent=opencode，取最新图（≤120s）
python -m baibao agent_image

# 不限时效，取库里最新一张
python -m baibao agent_image --max-age 0

# 仅列候选（调试）
python -m baibao agent_image --list

# 用分隔符包裹结果，便于在夹杂日志时精准截取
python -m baibao agent_image --delim __IMG__ 2>$null
```

### 输出

- 结果路径走 **stdout**，诊断日志走 **stderr**；`--delim` 可包裹结果便于精准截取。
- 以只读方式（SQLite URI `mode=ro`）打开会话库，绝不锁住正在写入的 agent 进程。
- 失败时 stdout 输出 `(未找到图片：…)` 标记，去掉 `2>$null` 可看 stderr 详细报错。

### 扩展：新增 agent 适配器

实现一个 `AgentImageAdapter` 子类（覆盖 `name`/`default_db_path`/`fetch_latest_sql`/`list_sql`/`parse_row` 等），注册到 `_AGENTS` 字典即可，命令侧无需改动。

---

## agent_memory

AI 记忆操作，子命令丰富、有独立的鉴权/去重概念。本篇只给入口，完整语义、子命令详解、配置与已知坑见 [agent_memory.md](./ai/agent_memory.md)。

### 子命令一览

| 子命令 | 功能 |
|------|------|
| `init` | 幂等建表 + 自检计数 |
| `remember` | 记一条事实（INSERT，含 scope+title 去重提示） |
| `recall` | 模糊检索（多关键词多字段加权，命中自动累加计数） |
| `update` | 按 id 部分更新 |
| `forget` | 按 id 软删除 |
| `get` | 按 id 取单条 |
| `list` | 浏览（按 scope/category 过滤，不计命中次数） |
| `count` | 统计条数 |

```bash
python -m baibao am init
python -m baibao am remember --scope myproj --category quirk --title "X 坑" --content "..."
python -m baibao am recall 登录 超时
```

> 完整选项、身份与角色（`--owner`/`--shared`）、去重维度（`--dedup`）见 [agent_memory.md](./ai/agent_memory.md)。

---

## agent_prompt

AI prompt 模板库（存/搜/取/渲染给 AI 的任务 prompt 模板）。完整子命令、块标记语法、鉴权模型见 [agent_prompt.md](./ai/agent_prompt.md)。

### 子命令一览

| 子命令 | 功能 |
|------|------|
| `init` | 初始化模板库 |
| `save` | 保存模板 |
| `list` | 列出模板 |
| `search` | 搜索模板 |
| `get` | 取模板全文 |
| `render` | 渲染模板（变量替换 + 块裁剪） |
| `update` | 更新模板 |
| `forget` | 删除模板 |
| `export` / `import` | 导出/导入 |
| `tags` / `stats` | 标签统计 |

```bash
python -m baibao ap init
python -m baibao ap save --name code-review --content-file prompt.txt
python -m baibao ap render code-review --var lang=python
```

> 完整选项与块标记语法 `<!-- @block:x | default:on/off -->` 见 [agent_prompt.md](./ai/agent_prompt.md)。

---

## plan_task

AI 计划任务（建任务/拆步骤/认领/收口/断点续跑）。完整子命令、状态机、重试预算、心跳僵尸检测见 [plan_task.md](./ai/plan_task.md)。

### 子命令一览

| 子命令 | 功能 |
|------|------|
| `init` | 初始化任务库 |
| `create` | 创建任务 |
| `plan` | 规划步骤 |
| `step add` | 添加步骤 |
| `claim` / `finish` / `fail` | 认领/完成/失败步骤 |
| `heartbeat` | 续命心跳 |
| `status` / `list` | 查看状态/列表 |
| `pause` / `resume` / `cancel` | 暂停/恢复/取消 |
| `retry` / `skip` / `sweep` | 重试/跳过/清扫僵尸 |
| `artifact add` / `artifact list` | 产物登记 |
| `event list` | 事件流水 |
| `template save` / `template list` | 任务蓝图模板 |

```bash
python -m baibao pt init
python -m baibao pt create --title "重构 OCR 模块" --description "..."
python -m baibao pt status <task_id>
```

> 完整选项、续跑上下文包、产物登记见 [plan_task.md](./ai/plan_task.md)。

---

## mojibake

检测/修复源文件中的乱码（UTF-8 字节被当作 GBK/CP936 解读后又存成 UTF-8 型，中文项目常见，如"鎸囧畾闆嗙兢鍚嶇О"）。通过往返还原法修复：把乱码文本按 GB18030 编码取字节，再按 UTF-8 解码。用 GB18030 而非 GBK 是因为 GBK 无法编码 PUA 区字符。

### 用法

```bash
python -m baibao mojibake [路径] [选项]    # 默认路径为当前目录
```

### 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `路径` | 路径 | 否 | `.` | 要扫描的目录或文件 |
| `-f, --fix` | 开关 | 否 | 关 | 修复模式（就地修改原文件）；默认只检测 |
| `--clean-fffd MODE` | 枚举 | 否 | — | 清理 U+FFFD 残留：`conservative`（仅删孤立 U+FFFD）/ `aggressive`（U+FFFD+'?' 替换为 '：'，约 80% 准确） |
| `--dry-run` | 开关 | 否 | 关 | 预览模式（只打印将发生的改动，不写盘）；常与 `--fix` 搭配 |
| `-s, --suffix EXT` | str | 否 | `.java` | 扫描的文件后缀；可多次指定扫描多种类型 |

### 示例

```bash
# 检测当前目录下所有 .java
python -m baibao mojibake

# 检测指定目录
python -m baibao mojibake src/

# 检测单个文件
python -m baibao mojibake File.java

# 修复 src/ 下所有文件
python -m baibao mojibake --fix src/

# 修复 + 清理残留
python -m baibao mojibake --fix --clean-fffd aggressive src/

# 预览将要修复的内容（不写盘）
python -m baibao mojibake --fix --dry-run src/

# 扫描其他后缀（可多次 -s）
python -m baibao mojibake data/ -s .txt -s .md
```

### 输出

- 检测结果走 **stdout**（便于管道），日志走 **stderr**。
- 单文件无乱码输出 `[OK] <path>`；检测到输出 `[WARN]` 加行号与片段；修复输出 `[FIX]`/`[DRY-RUN-FIX]` 加 run 数。

---

## move_java

迁移 Java 源文件到新包（自动改 package + 同步引用方的 import）。支持包重构、main↔test 跨源根迁移、修正测试包路径与被测类不一致。

### 用法

```bash
python -m baibao move_java <src_relpath> <dest_relpath> [--dest-root ROOT]    # 单文件
python -m baibao move_java --map <csv_file>                                   # 批量
```

### 选项

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `src` | 路径 | 与 `--map` 二选一 | — | 源文件相对路径（相对源根） |
| `dest` | 路径 | 与 `--map` 二选一 | — | 目标文件相对路径 |
| `--map FILE` | 路径 | 与单文件二选一 | — | 批量迁移 CSV 映射文件，每行 `src\|dest[\|dest_root]`，`#` 开头为注释 |
| `--dest-root ROOT` | 路径 | 否 | `src/main/java` | 目标源根（跨根迁移用，如 `src/test/java`） |
| `--src-roots ROOTS` | str | 否 | `src/main/java,src/test/java` | 扫描 import 的源根列表，逗号分隔 |
| `--dry-run` | 开关 | 否 | 关 | 预演模式（不写盘） |

### 示例

```bash
# 单文件迁移
python -m baibao move_java store/code/csv/ApacheCsvDemo.java store/code/demo/csv/ApacheCsvDemo.java

# 跨源根迁移（main -> test）
python -m baibao move_java store/code/barcode/QRCodeDemo.java \
                         store/code/demo/barcode/QRCodeDemo.java \
                         --dest-root src/test/java

# 批量迁移（CSV 映射文件）
python -m baibao move_java --map moves.csv

# 预演（不写盘）
python -m baibao move_java --map moves.csv --dry-run
```

CSV 文件示例（`moves.csv`）：

```text
# 普通迁移
store/code/csv/ApacheCsvDemo.java|store/code/demo/csv/ApacheCsvDemo.java
# 跨源根迁移（第三列为 dest_root）
store/code/barcode/QRCodeDemo.java|store/code/demo/barcode/QRCodeDemo.java|src/test/java
```

### 输出

- 成功日志输出 `[OK] src -> dest`；源不存在或 src==dest 输出 `[SKIP]`。
- 批量模式从 CSV 加载条目数，完成后汇总成功/跳过数。

---

## autotest

Web 自动化测试（Playwright）辅助工具。需安装 `baibao[autotest]` extra。
完整语义见 [autotest.md](./test/autotest.md)。

### 用法

```text
python -m baibao autotest probe TARGET [选项]

TARGET            路由（如 it-asset、#/it-asset）或完整 URL；
                  Git Bash 下用纯路由（不带开头 # 或 /），免疫 MSYS 路径转换
--role NAME       角色名，登录态缓存 <role>.json（默认: admin）
--base-url URL    被测系统基础地址（默认: 环境变量 BASE_URL）
--auth-dir DIR    登录态缓存目录（默认: .auth）
--username NAME   登录用户名（默认: 环境变量 ADMIN_USERNAME）
--password PASS   登录密码（默认: 环境变量 ADMIN_PASSWORD）
--captcha CODE    验证码（默认: 环境变量 CAPTCHA_VALUE）
--click-label L   提取前先点击的表单项标签，可重复（展开下拉拿实际选项）
--brief           超简略模式：只留骨架，去掉当前值/首行样本/分页数据
--js CODE         自定义提取 JS（表达式或箭头函数），返回结果的紧凑 JSON
--js-file PATH    从 UTF-8 文件读自定义 JS（传 - 读 stdin）；含引号 JS 用此项
--headless        无头模式运行（默认有头）
--out FILE        摘要同时写入指定文件（UTF-8）
```

### 示例

```bash
# 输出页面结构摘要（表单/表格/按钮/弹窗/消息）
python -m baibao autotest probe "#/oa/asset"

# 展开下拉后提取实际选项
python -m baibao autotest probe "#/purchase/stock" --click-label 供应商

# 超简略模式（骨架）
python -m baibao autotest probe "#/oa/asset" --brief

# 自定义提取：当前页表格行数（紧凑 JSON 输出）
python -m baibao autotest probe "#/oa/asset" --js 'document.querySelectorAll(".el-table__row").length'

# 丢弃日志拿干净 stdout（结果/日志分离约定）
python -m baibao autotest probe "#/oa/asset" 2>/dev/null
```

规划中（未实现，勿依赖）：`login`（预热/刷新角色登录态缓存）、`doctor`（环境自检）。

---

## 环境说明

### 镜像源

`pip_install` 和 `pip_upgrade` 内置多个镜像源，自动尝试：清华大学镜像 → 阿里云镜像 → 官方 PyPI。某源失败自动切换到下一个，无需手动配置。手动安装命令也可末尾加 `-i https://pypi.tuna.tsinghua.edu.cn/simple/` 加速。

### stdout / stderr 分离约定

凡结果型命令（`ocr`、`rdb query`、`agent_image` 等）遵循同一约定：**结果走 stdout，日志走 stderr**。脚本化调用时加 `2>$null`（PowerShell）或 `2>/dev/null`（bash）丢弃日志即可拿干净输出；担心框架偶发把日志写到 stdout 时，用 `--delim` 包裹结果精准截取。

## 架构说明

该模块采用命令模式设计：

- `Command` 基类（来自 `pykunlun.cli`）：定义命令接口（`name`、`abbr`、`description`、`usage`、`execute`）。
- `CommandManager`：管理命令注册和执行，支持命令缩写、帮助命令、线程安全。
- 具体命令类：在 `baibao/cli/*_command.py` 中实现，于 `cli/__init__.py` 中 `command_manager.register(...)` 注册。

### 扩展自定义命令

要添加自定义命令，只需：

1. 创建继承 `Command` 的类，实现 `name` / `description` / `usage` / `execute`。
2. 在 `cli/__init__.py` 中注册命令。

```python
from pykunlun.cli import Command

class MyCommand(Command):
    @property
    def name(self) -> str:
        return "my_command"

    @property
    def description(self) -> str:
        return "我的自定义命令"

    @property
    def usage(self) -> str:
        return "python -m baibao my_command [args]"

    def execute(self, ctx):
        # 实现命令逻辑
        pass
```

# agent_memory 命令行工具

> AI 记忆：把"事实类项目知识"持久化进可检索的记忆库，让 AI 跨会话"记住/回忆/更新/遗忘"。
> 缩写 `am`，与 `agent_prompt` / `plan_task` 同库（`ai_agent` 实例）部署，复用同一套 rdb 实例与 owner 鉴权模型。

```bash
python -m baibao agent_memory <子命令> [选项]
python -m baibao am <子命令> [选项]        # 缩写
```

## 命令清单

| 子命令 | 功能 | 用法一行 |
|--------|------|----------|
| `init` | 幂等建表 + 自检计数 | `am init` |
| `remember` | 记一条事实（INSERT，含 scope+title 去重提示） | `am remember --scope S --category C --title T --content/--content-file F` |
| `recall` | 模糊检索（多关键词多字段加权，命中自动累加计数） | `am recall <关键词...> [--scope S] [--category C]` |
| `update` | 按 id 部分更新 | `am update <id> [--title T --content ...]` |
| `forget` | 按 id 软删除 | `am forget <id>` |
| `get` | 按 id 取单条 | `am get <id>` |
| `list` | 浏览（按 scope/category 过滤，不计命中次数） | `am list [--scope S] [--category C]` |
| `count` | 统计条数 | `am count [--all]` |

---

## 身份与角色

`owner` 是唯一鉴权字段（与 `agent_prompt` 同模型）；`machine` / `agent_name` 是**纯标签**，仅用于 `remember` 盖章与 machine 维度去重，**不参与鉴权与过滤**，`recall` 时随结果返回供 AI 自判来源。

| 数据归属 | owner | 可见性 | 改动要求 |
|----------|-------|--------|----------|
| 共享 | `NULL` | 全员可见可改 | 改/删共享数据须显式 `--shared` |
| 个人 | 有值 | 仅本人 | — |

`--shared` 切换为**共享角色**：owner 被忽略，仅查看/操作 owner 为空的共享数据，个人数据不可见不可改。

> `owner` 仅作鉴权；`machine` / `agent_name` 不鉴权、不过滤——这与 `plan_task` 的 `owner`（仅作标签不鉴权）不同。鉴权模型详见 `pykunlun.ai_agent.memory` 的 `visibility_clause` / `permission_clause`。

### 去重维度（仅 `remember`）

`--dedup {auto,machine,global}`：

| 维度 | 判重键 | 适用语义 |
|------|--------|----------|
| `auto`（默认） | 按 category 自动：路径类（`file-path`）按 machine，其余按 global | 通用知识全局唯一，路径类按机器隔离 |
| `machine` | scope + title + machine | 本机路径类事实隔离判重（要求 machine 已绑定） |
| `global` | scope + title | 全局通用知识判重 |

`auto` 时路径类 category 但未配置 machine 会自动降级 `global` 并提示；`--dedup machine` 但未解析到 machine 直接报错拒绝。

### category 枚举

`--category` 取值（来自 `pykunlun.ai_agent`）：`convention`（约定）、`decision`（决策）、`file-path`（路径，唯一的路径类）、`history`（历史）、`no-go`（禁忌）、`other`（其他）、`quirk`（怪癖）。

---

## 配置

配置三层优先级：**命令行标志 > 环境变量 > 配置文件**。

配置文件 `.baibao/agent_memory.config`（JSON，当前目录优先，再 `~/.baibao/`，不读技能目录，避免多 agent 安装点配置歧义）：

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|---------|------|--------|------|
| `rdb_name` | `AGENT_MEMORY_DB` | str | `ai_agent` | rdb 实例名，复用 `~/.baibao/rdb.config` 已有可写实例 |
| `owner` | `AGENT_MEMORY_OWNER` | str | - | 当前用户标识；未配置以"无身份"运行（仅共享域可见可写） |
| `owner_group` | `AGENT_MEMORY_OWNER_GROUP` | str | - | 当前团队/组（标签，仅 remember 盖章用） |
| `machine` | `AGENT_MEMORY_MACHINE` | str | `socket.gethostname()` | 当前物理机标识（标签）；末尾兜底自动探测 |
| `agent_name` | `AGENT_MEMORY_AGENT_NAME` | str | - | 当前 agent 外壳标识（标签）；无自动探测源，未配置则为空 |

```json
{
  "rdb_name": "ai_agent",
  "owner": "你的用户标识",
  "owner_group": "你的团队",
  "machine": "my-host"
}
```

---

## 子命令

### init — 建表与自检

```bash
python -m baibao am init
```

幂等建表 + 自检，打印当前可见记忆数。

### remember — 记一条事实

```bash
# 内联内容
python -m baibao am remember --scope myproj --category quirk \
  --title "登录接口超时坑" --content "并发>5 时网关 504，需加熔断" --keywords "登录,超时"

# 从文件读内容（含引号/特殊字符/超长内容时推荐）
python -m baibao am remember --scope myproj --category file-path \
  --title "证书路径" --content-file facts/cert.txt --machine my-host

# 从 stdin 读
cat facts/note.md | python -m baibao am remember --scope myproj --category decision \
  --title "选 PostgreSQL" --content-file -

# 共享记忆（团队共享，加 --shared）
python -m baibao am remember --scope myproj --category convention \
  --title "分支命名规范" --content "feature/<ticket>-<slug>" --shared

# 路径类按本机隔离判重
python -m baibao am remember --scope myproj --category file-path \
  --title "本地配置文件" --content "C:/Users/me/.baibao/..." --dedup machine
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--scope` | str | 是 | - | 作用域（项目名/模块名） |
| `--category` | 枚举 | 是 | - | 事实类型，见 [category 枚举](#category-枚举) |
| `--title` | str | 是 | - | 一句话摘要 |
| `--content` | str | 二选一 | - | 完整内容；与 `--content-file` 互斥 |
| `--content-file` | 路径 | 二选一 | - | 从 UTF-8 文件读内容；`-` 读 stdin（绕开 shell argv 引号剥离） |
| `--keywords` | str | 否 | `""` | 逗号分隔的关键词/标签 |
| `--source` | 枚举 | 否 | `user-told` | 来源：`user-told` / `code-derived` / `inferred` |
| `--confidence` | int | 否 | `80` | 置信度 0~100 |
| `--pinned` | 枚举 | 否 | `0` | 是否置顶：`0` / `1` |
| `--force` | 开关 | 否 | 关 | 同 scope+title 已存在时仍追加（否则拒绝并提示已有 id） |
| `--dedup` | 枚举 | 否 | `auto` | 去重维度：`auto` / `machine` / `global`，见[去重维度](#去重维度仅-remember) |
| `--owner` | str | 否 | 环境变量/配置 | 当前用户标识 |
| `--owner-group` | str | 否 | 环境变量/配置 | 当前团队/组（标签） |
| `--machine` | str | 否 | 自动探测 | 当前物理机标识（标签） |
| `--agent-name` | str | 否 | 环境变量/配置 | 当前 agent 外壳标识（标签） |
| `--shared` | 开关 | 否 | 关 | 存共享记忆必填；个人记忆不可加 |

同 scope+title 已存在（在可见范围内，按 dedup 维度判重）且未加 `--force` 时拒绝，并提示已有 id，建议改用 `update`。

### recall — 模糊检索

```bash
python -m baibao am recall 登录 超时
python -m baibao am recall 证书 --scope myproj --category file-path --limit 10
python -m baibao am recall 部署 --full              # 不截断，content 全文
python -m baibao am recall 部署 --format table      # 表格输出
```

多关键词多字段加权检索，**命中自动累加命中计数**（区别于 `list` 的浏览）。

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | str[] | 是 | - | 查询关键词，空格分隔 |
| `--scope` | str | 否 | - | 限定作用域 |
| `--category` | str | 否 | - | 限定事实类型 |
| `--limit` | int | 否 | `20` | 最多返回条数 |
| `--snippet` | int | 否 | `300` | content 预览字数（折叠换行+截断+内联指示）；`--full` 时忽略 |
| `--full` | 开关 | 否 | 关 | 不截断，content 返回全文+原样换行 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

### update — 部分更新

```bash
python -m baibao am update 42 --title "登录接口超时坑 v2" --confidence 95
python -m baibao am update 42 --content-file facts/new.md
python -m baibao am update 42 --pinned 1 --shared          # 改共享记忆加 --shared
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | int | 是 | - | 待更新的记忆 id |
| `--scope` | str | 否 | - | 新作用域 |
| `--category` | 枚举 | 否 | - | 新事实类型 |
| `--title` | str | 否 | - | 新标题 |
| `--content` | str | 否 | - | 新内容内联；与 `--content-file` 互斥 |
| `--content-file` | 路径 | 否 | - | 新内容文件；`-` 读 stdin |
| `--keywords` | str | 否 | - | 新关键词 |
| `--source` | 枚举 | 否 | - | 新来源 |
| `--confidence` | int | 否 | - | 新置信度 |
| `--pinned` | 枚举 | 否 | - | 新置顶：`0` / `1` |
| `--shared` | 开关 | 否 | 关 | 改共享记忆必填 |

白名单字段部分更新；`--content` 与 `--content-file` 互斥，都不给则不更新 content；未指定任何字段报错。

### forget — 软删除

```bash
python -m baibao am forget 42
python -m baibao am forget 42 --shared          # 软删共享记忆加 --shared
```

按 id 软删除。未命中（不存在/已删除/无权限）返回失败。

### get — 取单条

```bash
python -m baibao am get 42
python -m baibao am get 42 --format json         # 缩进多行便于人读
```

按 id 取单条全文。未找到（可能不可见）不报错，仅日志提示。`--format` 同 `recall`。

### list — 浏览

```bash
python -m baibao am list --scope myproj --limit 50
python -m baibao am list --category quirk --format table
```

浏览可见范围（按 pinned/最近使用排序），**不计命中次数**（`touch=False`）。

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--scope` | str | 否 | - | 限定作用域 |
| `--category` | str | 否 | - | 限定事实类型 |
| `--limit` | int | 否 | `20` | 最多返回条数 |
| `--snippet` | int | 否 | `300` | content 预览字数；`--full` 时忽略 |
| `--full` | 开关 | 否 | 关 | 不截断，content 全文 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

### count — 统计

```bash
python -m baibao am count
python -m baibao am count --all                  # 含软删除项
```

打印可见记忆条数。

---

## 输出

- **默认格式 `jsonl`**（一行一对象，省 token）；`--format json` 缩进多行便于人读，`csv` / `table` 用于浏览。
- 长 content 默认 300 字 snippet 预览：折叠空白（含换行）为单行预览，超长截断并追加内联指示 `…（+N字，get <id> 看全文）`；`--full` 输出全文+原样换行，`--snippet N` 调整截断长度。
- 结果用 `--delim` 分隔符包裹（前后各占一行），便于在夹杂日志时精准截取（由 `ctx.print_delim` 控制）。
- 退出码：`0` 成功；`1` 参数错误 / 查重拒绝 / 鉴权失败。
- 失败时 stderr 输出可行动提示。

---

## 已知坑与版本兼容

| 现象 | 根因 | 解法 |
|------|------|------|
| `remember` 个人记忆但报"无法解析身份" | 未配置 `owner`，以无身份运行仅共享域可写 | 配置 `AGENT_MEMORY_OWNER` 或 `.baibao/agent_memory.config` 的 `owner`，或加 `--owner` |
| 改/删共享记忆报鉴权失败 | 改共享数据须显式 `--shared` | 加 `--shared` |
| `--dedup machine` 报错"未解析到 machine" | `--dedup machine` 要求 machine 已绑定，但未配置也未探测到 | 用 `--machine` 指定、设 `AGENT_MEMORY_MACHINE`、配置文件配 `machine`，或改 `--dedup global` |
| 路径类 category 判重未按机器隔离 | `auto` 时路径类需 machine 才走 machine 维度 | 配置 `machine`；日志会提示"按全局判重" |
| Windows shell 引号剥离长内容 | argv 引号处理问题（CRT 在 Python 之前解析） | 用 `--content-file` / `-` 读 stdin，不内联长文本 |
| `recall` 结果里 machine/agent_name 不稳定 | 这两个是纯标签不参与过滤，仅随命中记录返回 | 视作来源提示，不做强一致依赖 |

### 良性告警

| 告警 | 含义 | 是否需处理 |
|------|------|-----------|
| `AGENT_MEMORY_OWNER 未配置，以无身份运行` | 仅共享域可见可写，不能记个人记忆 | 否（如需记个人记忆再配置） |
| `路径类 category=X 但未配置 machine，按全局判重` | auto 维度降级为 global | 否（如需本机隔离再配 machine） |

### 版本兼容

| 组件 | 要求 | 说明 |
|------|------|------|
| 数据库 | MySQL / PostgreSQL / SQLite | `RdbMemoryStore` 方言自适应 |
| Python | ≥ 3.10 | 依赖 pykunlun / baibao |
| rdb 实例 | 复用 `ai_agent` 可写实例 | 与 `agent_prompt` / `plan_task` 同库，无需新增连接 |

---

## 完整示例

把一个项目怪癖记入记忆、检索、更新、遗忘的端到端流程：

```bash
# 1. 初始化（幂等）
python -m baibao am init

# 2. 记一条个人怪癖
python -m baibao am remember --scope myproj --category quirk \
  --title "登录接口并发超时" \
  --content "并发>5 时网关 504，需加熔断或降并发" \
  --keywords "登录,超时,网关" --confidence 90 --pinned 1

# 3. 检索回忆（命中会累加计数）
python -m baibao am recall 登录 超时

# 4. 取全文
python -m baibao am get 1

# 5. 更新置信度并置顶
python -m baibao am update 1 --confidence 95

# 6. 浏览某作用域
python -m baibao am list --scope myproj --format table

# 7. 遗忘（软删除）
python -m baibao am forget 1
```

团队共享规范示例：

```bash
# 存共享约定（加 --shared）
python -m baibao am remember --scope myproj --category convention \
  --title "分支命名规范" --content "feature/<ticket>-<slug>" --shared

# 全员可 recall 共享记忆
python -m baibao am recall 分支命名
```

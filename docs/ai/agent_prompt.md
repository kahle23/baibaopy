# agent_prompt 命令行工具

> AI prompt 模板库：把"给 AI 的任务 prompt"从散落的本地文本文件，升级为团队共享、可搜索、
> 可参数化、可按场景裁剪的模板库。缩写 `ap`，与 `plan_task` / `agent_memory` 同库（`ai_agent` 实例）部署。

```bash
python -m baibao agent_prompt <子命令> [选项]
python -m baibao ap <子命令> [选项]        # 缩写
```

## 命令清单

| 子命令 | 功能 | 用法一行 |
|--------|------|----------|
| `init` | 幂等建表 + 自检计数 | `ap init` |
| `save` | 入库一条模板（同名查重，`--force` 覆盖） | `ap save --name N --title T --content-file F [--shared]` |
| `list` | 浏览模板（按取用热度排序，不计取用次数） | `ap list [--tag T] [--limit N]` |
| `search` | 多关键词加权模糊检索 | `ap search <关键词...> [--tag T]` |
| `get` | 取"即用包"（元信息 + 变量清单 + 块清单 + 正文） | `ap get <name>` |
| `render` | 确定性渲染：填变量 / 裁剪块 / 剥离标记 | `ap render <name> --set k=v [--with 块] [--without 块] [--clip]` |
| `update` | 白名单字段部分更新 | `ap update <name> [--title T --tags ...]` |
| `forget` | 软删除（is_deleted=1） | `ap forget <name>` |
| `export` | 导出 markdown 交换格式 | `ap export <name> --out F` |
| `import` | 从 markdown 导入 | `ap import --file F [--force]` |
| `tags` | 标签聚合 | `ap tags` |
| `stats` | 取用排行 | `ap stats [--limit N]` |

---

## 模板标记语法

写模板时用两种标记，`save` / `update` / `import` 写入正文前会强校验（坏标记当场报错，不入库）。

### 变量 `{{name}}`

占位必填变量。`save` 时自动扫描生成清单；`--vars-file` 可补 `desc/example/required/default` 元信息。

- `required:false` + `default` 的变量缺值时用默认值；无默认则保留占位符原样。
- 变量只在**未被裁剪的文本**里要求提供——默认 off 的块被裁掉后，块内变量不再必填。

### 可选块

```text
请以 {{language}} 专家身份审查以下代码：
<!-- @block:concurrent-check | default:off | 仅涉及多线程/并发/异步场景时保留 -->
3. 并发安全（{{framework}} 的线程安全性、锁范围、竞态条件）
<!-- @endblock:concurrent-check -->
```

- `default:on/off` 声明默认去留，注释写清"什么场景保留"给 AI 看。
- 块外内容恒定保留；不支持嵌套。
- `get` 输出解析后的块清单；`render` 的 `--with/--without` 显式覆盖 default。
- 渲染后剥离全部块标记注释，删除块留下的连续空行折叠为最多 1 个空行。

---

## 身份与共享

`owner` 是唯一鉴权字段，与 `agent_memory` 同模型：

| 模板归属 | owner | 可见性 | 改动要求 |
|----------|-------|--------|----------|
| 共享 | `NULL` | 全员可见可改 | 改动须显式 `--shared` |
| 个人 | 有值 | 仅本人 | — |

行为约定：

- **防默认值陷阱**：`save` 个人模板但解析不到身份 → 直接报错（不会静默落共享）。
- **覆盖更新**（`--force`）按目标行归属鉴权：共享行须 `--shared`，他人个人模板不可覆盖。
- 读可见性：共享 + 本人个人；`created_by/updated_by/updated_at` 全程留痕（多人 last-write-wins）。
- `name` 全局唯一且软删除后仍占用（防误删误建同名的版本混淆）。

---

## 配置

配置三层优先级：**命令行标志 > 环境变量 > 配置文件**。

配置文件 `.baibao/agent_prompt.config`（JSON，当前目录优先，再 `~/.baibao/`，不读技能目录）：

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|---------|------|--------|------|
| `rdb_name` | `AGENT_PROMPT_DB` | str | `ai_agent` | rdb 实例名，复用 `~/.baibao/rdb.config` 已有可写实例 |
| `owner` | `AGENT_PROMPT_OWNER` | str | - | 当前用户标识；未配置以"无身份"运行（仅共享模板可见） |

```json
{
  "rdb_name": "ai_agent",
  "owner": "你的用户标识"
}
```

---

## 子命令

### init — 建表与自检

```bash
python -m baibao ap init
```

幂等建表 + 自检，打印可见模板数。

### save — 入库

```bash
# 从文件读正文
python -m baibao ap save --name code-review --title "代码审查 prompt" \
  --content-file prompts/code-review.md --tags "review,code" --shared

# 从 stdin 读正文
cat prompts/code-review.md | python -m baibao ap save --name code-review \
  --title "代码审查 prompt" --content-file - --shared

# 覆盖更新同名模板（保留 id/归属/创建信息）
python -m baibao ap save --name code-review --title "代码审查 prompt v2" \
  --content-file prompts/code-review-v2.md --shared --force
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--name` | str | 是 | - | 模板名（全局唯一）；同名已存在则拒绝，需 `--force` 覆盖 |
| `--title` | str | 是 | - | 显示标题 |
| `--content` | str | 二选一 | - | 正文内联；与 `--content-file` 互斥 |
| `--content-file` | str | 二选一 | - | 正文文件路径；`-` 读 stdin |
| `--description` | str | 否 | `""` | 模板说明 |
| `--tags` | str | 否 | `""` | 标签，逗号分隔 |
| `--vars-file` | str | 否 | - | 变量元信息 JSON（补 desc/example/required/default） |
| `--force` | 开关 | 否 | 关 | 覆盖更新同名模板，保留 id/归属/创建信息 |
| `--shared` | 开关 | 否 | 关 | 存共享模板必填；个人模板不可加 |
| `--owner` | str | 否 | 环境变量/配置 | 当前用户标识 |

写入即强校验块标记：未闭合 / 嵌套 / 头尾不匹配当场抛错，不入库。

### list — 浏览

```bash
python -m baibao ap list --limit 20
python -m baibao ap list --tag review
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--tag` | str | 否 | - | 按标签过滤 |
| `--limit` | int | 否 | 50 | 返回条数 |
| `--snippet` | int | 否 | 300 | content 预览截断字数 |
| `--full` | 开关 | 否 | 关 | 输出 content 全文 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

按取用热度排序，**浏览不计取用次数**。

### search — 模糊检索

```bash
python -m baibao ap search 代码 审查
python -m baibao ap search code review --tag review --limit 10
```

加权模糊检索权重：name 4 / title 3 / tags 2 / description 2 / content 1。

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | str[] | 是 | - | 关键词，可多个 |
| `--tag` | str | 否 | - | 按标签过滤 |
| `--limit` | int | 否 | 20 | 返回条数 |
| `--snippet` | int | 否 | 300 | content 预览截断字数 |
| `--full` | 开关 | 否 | 关 | 输出 content 全文 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

### get — 取即用包

```bash
python -m baibao ap get code-review
python -m baibao ap get code-review --no-count    # 不累加取用计数
```

输出"即用包"：元信息 + 变量清单 + 块清单（默认开关 + 场景注释）+ 正文全文。默认取用计数 +1。

命中规则：共享 + 本人个人模板可见；未命中（不存在 / 已软删 / 他人个人模板）报错失败。

### render — 确定性渲染

```bash
# 填变量 + 保留默认 off 的并发检查块
python -m baibao ap render code-review \
  --set language=Python --set framework=Django --with concurrent-check

# 从 JSON 文件批量填变量
python -m baibao ap render code-review --set-file vars.json --clip

# 显式裁剪块（覆盖 default:on）
python -m baibao ap render code-review --set language=Python --without concurrent-check
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | str | 是 | - | 模板名 |
| `--set` | str | 否 | - | 填变量 `k=v`，可多次 |
| `--set-file` | str | 否 | - | 变量 JSON 对象文件 |
| `--with` | str | 否 | - | 强制保留的块名，逗号分隔 |
| `--without` | str | 否 | - | 强制裁剪的块名，逗号分隔 |
| `--clip` | 开关 | 否 | 关 | 渲染结果写入系统剪贴板 |
| `--no-count` | 开关 | 否 | 关 | 不累加取用计数 |

渲染流程：填变量（缺必填报错并列出）→ 按 `--with/--without` 裁剪块（否则按 default）→ 剥离全部块标记 → 折叠连续空行。`--clip` 把结果写剪贴板，正文始终同时打印到 stdout。

### update — 部分更新

```bash
python -m baibao ap update code-review --title "代码审查 prompt v3" --tags "review,code,v3"
python -m baibao ap update code-review --content-file prompts/code-review-v3.md --shared
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | str | 是 | - | 模板名 |
| `--title` | str | 否 | - | 新标题 |
| `--description` | str | 否 | - | 新说明 |
| `--tags` | str | 否 | - | 新标签 |
| `--content` | str | 否 | - | 新正文内联；与 `--content-file` 互斥 |
| `--content-file` | str | 否 | - | 新正文文件；`-` 读 stdin |
| `--vars-file` | str | 否 | - | 新变量元信息 JSON |
| `--shared` | 开关 | 否 | 关 | 改共享模板必填 |
| `--owner` | str | 否 | 环境变量/配置 | 当前用户标识 |

白名单字段部分更新；正文更新时自动重扫变量、同样强校验块标记。正文更新未给 `--vars-file` 时按新正文自动重扫。

### forget — 软删除

```bash
python -m baibao ap forget code-review --shared
```

软删除（is_deleted=1），`name` 仍占用。

### export / import — 备份迁移

```bash
# 导出
python -m baibao ap export code-review --out backups/code-review.md

# 导入
python -m baibao ap import --file backups/code-review.md
python -m baibao ap import --file - --force < backups/code-review.md   # stdin + 覆盖
```

markdown 交换格式（frontmatter + 正文）。`import` 同名查重拒绝，`--force` 覆盖。

### tags / stats — 聚合

```bash
python -m baibao ap tags
python -m baibao ap stats --limit 20
```

`tags` 标签聚合；`stats` 取用排行（`--limit` 默认 10）。

---

## 输出

- **默认格式 `jsonl`**（一行一对象，省 token）；`--format json` 缩进多行便于人读，`csv` / `table` 用于浏览。
- 长 content 默认 300 字 snippet 预览（折叠换行 + 截断 + `…（+N字，get <name> 看全文）` 指引），`--full` 输出全文，`--snippet N` 调整截断长度。
- 退出码：`0` 成功；`1` 参数错误 / 查重拒绝 / 鉴权失败 / 块标记校验失败。
- 命令返回 bool；失败时 stderr 输出可行动提示。

---

## 已知坑与版本兼容

| 现象 | 根因 | 解法 |
|------|------|------|
| `save` 个人模板报"无法解析身份" | 未配置 `owner`（环境变量 / 配置文件都没给），防默认值陷阱拦截 | 配置 `AGENT_PROMPT_OWNER` 或 `.baibao/agent_prompt.config` 的 `owner`，或加 `--owner` |
| `save --force` 共享模板报鉴权失败 | 覆盖共享行须显式 `--shared` | 加 `--shared` |
| `render` 报"缺必填变量" | 未给齐未被裁剪文本里的 `{{var}}` | 按 `get` 的变量清单补 `--set`；或用 `--without` 裁掉含该变量的块；或给变量配 `required:false`+`default` |
| `get` 历史模板 blocks 显示 `parse_error` | 强校验上线前的历史坏数据 | 修正正文块标记后 `update`；`render` 该模板会报错 |
| Windows PowerShell 剪贴板中文乱码 | `clip.exe` 按 GBK 编码 | 已优先用 ctypes 直调 Win32 剪贴板（CF_UNICODETEXT）；仍乱码时正文已打印到 stdout，手动复制 |
| Windows shell 引号剥离长正文 | argv 引号处理问题 | 用 `--content-file`/`-` 读 stdin，不内联长文本 |

### 良性告警

| 告警 | 含义 | 是否需处理 |
|------|------|-----------|
| `AGENT_PROMPT_OWNER 未配置，以无身份运行` | 仅共享模板可见，不能存个人模板 | 否（如需存个人模板再配置） |

### 版本兼容

| 组件 | 要求 | 说明 |
|------|------|------|
| 数据库 | MySQL / PostgreSQL / SQLite | RdbPromptStore 方言自适应 |
| Python | ≥ 3.8 | 依赖 pykunlun / baibao |
| rdb 实例 | 复用 `ai_agent` 可写实例 | 与 `plan_task` / `agent_memory` 同库，无需新增连接 |

---

## 完整示例

把一个带可选块的代码审查模板入库、检索、渲染给 AI 的端到端流程：

```bash
# 1. 初始化
python -m baibao ap init

# 2. 存模板（带并发检查可选块，共享）
python -m baibao ap save --name code-review --title "代码审查 prompt" \
  --content-file prompts/code-review.md --tags "review,code" --shared

# 3. 检索确认
python -m baibao ap search 代码 审查

# 4a. 取即用包给 AI（AI 自行按场景裁剪块、补变量）
python -m baibao ap get code-review

# 4b. 或确定性渲染进剪贴板，贴给其他 AI 工具
python -m baibao ap render code-review \
  --set language=Python --set framework=Django --with concurrent-check --clip

# 5. 看取用排行
python -m baibao ap stats
```

模板正文示例（`prompts/code-review.md`）：

```text
请以 {{language}} 专家身份审查以下代码：
<!-- @block:concurrent-check | default:off | 仅涉及多线程/并发/异步场景时保留 -->
3. 并发安全（{{framework}} 的线程安全性、锁范围、竞态条件）
<!-- @endblock:concurrent-check -->
```

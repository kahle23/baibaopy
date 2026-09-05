# plan_task 命令行工具

> AI 计划任务能力：让 AI 以"断点可续、重试有据、全程留痕"的方式执行超出单会话承载能力的计划任务。
> 缩写 `pt`。数据库是唯一真相源，AI 会话只是可随时替换的执行单元——会话崩溃/断连后，新会话凭库里的状态无缝接手。

```bash
python -m baibao plan_task <子命令> [选项]
python -m baibao pt <子命令> [选项]        # 缩写
```

生命周期：`init → create → plan → (claim → finish|fail)* → status`
恢复入口：`sweep → list --status running → claim`（断点续跑）

## 命令清单

| 子命令 | 功能 | 用法一行 |
|--------|------|----------|
| `init` | 幂等建表 + 自检 | `pt init` |
| `create` | 建任务，输出 task_id | `pt create --title T --goal-file F` |
| `plan` | 批量导入步骤（JSON 数组） | `pt plan <task_id> --steps-file F` |
| `step add` | 单条加步骤 | `pt step add <task_id> --name N --instruction-file F` |
| `claim` | 原子认领下一步骤，输出续跑上下文包 | `pt claim <task_id>` |
| `finish` | 成功收口一次执行 | `pt finish <run_id> --output-file F --summary "..."` |
| `fail` | 失败上报（自动按预算决定重试/终败） | `pt fail <run_id> --error "..."` |
| `heartbeat` | 刷任务心跳 | `pt heartbeat <task_id>` |
| `status` | 任务总览（任务+步骤+进度+产物计数） | `pt status <task_id>` |
| `list` | 任务列表 | `pt list [--status running]` |
| `pause` / `resume` | 暂停 / 恢复 | `pt pause <task_id>` / `pt resume <task_id>` |
| `cancel` | 取消任务 | `pt cancel <task_id> [--reason R]` |
| `retry` | 手动重置失败步骤回 pending | `pt retry <step_id>` |
| `skip` | 跳过 pending 步骤 | `pt skip <step_id> [--reason R]` |
| `sweep` | 僵尸检测与恢复（幂等） | `pt sweep [--heartbeat-timeout-sec N]` |
| `artifact add` | 产物登记 | `pt artifact add <task_id> --path P` |
| `artifact list` | 产物查询 | `pt artifact list <task_id>` |
| `event list` | 事件流水查询 | `pt event list <task_id>` |
| `template save` | 把任务步骤存为模板蓝图 | `pt template save <task_id> --name N` |
| `template list` | 列模板 | `pt template list` |

---

## 核心概念

| 概念 | 说明 |
|------|------|
| 任务（instance） | 一次具体的计划任务，有目标、状态、重试预算、心跳 |
| 步骤（step） | "计划中要做的事"，按 seq 串行；状态 pending/running/succeeded/failed/skipped |
| 执行（run） | "实际的一次尝试"；失败重试 = 同一 step 新增一条 run，不污染 step 语义 |
| 续跑上下文包 | `claim` 返回的 dict，含当前 step + run_id + 前序步骤摘要，新会话接手所需的全部信息 |
| 重试预算 | `step.retry_count / max_retries`；默认 1（首次 + 重试 1 次），创建步骤时可覆盖 |

断点续跑 = 取"第一个非 succeeded 的步骤"继续派发。主会话只做 claim/派发/收口，不吃执行产物原文；每个步骤派全新子代理执行，主会话保持轻上下文。

任务状态流转：pending → running → completed/failed/cancelled（含 paused 中间态）。
步骤状态流转：pending → running → succeeded/failed/skipped（失败但预算未耗尽回 pending）。

---

## 配置

配置文件与命令行/环境变量各管一层：**配置文件只放环境事实（`rdb_name`/`owner`），调用者身份
（`session_id`/`agent_name`）由命令行标志或环境变量注入**。解析优先级：命令行标志 > 环境变量 > 配置文件。

配置文件 `.baibao/plan_task.config`（JSON，当前目录优先，再 `~/.baibao/`，不读技能目录）：

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|---------|------|--------|------|
| `rdb_name` | `PLAN_TASK_DB` | str | `ai_agent` | rdb 实例名；建议为计划任务单独注册别名（如 `plan_task`），与业务库、记忆库隔离 |
| `owner` | `PLAN_TASK_OWNER` | str | - | created_by 标签，不鉴权；未配置仅告警不阻断 |

`session_id` / `agent_name` 不进配置文件，调用时经 `--session-id` / `--agent-name` 标志或
`PLAN_TASK_SESSION_ID` / `PLAN_TASK_AGENT_NAME` 环境变量传入（盖章 run；不传落 NULL）。

```json
{
  "rdb_name": "ai_agent",
  "owner": "你的用户标识"
}
```

> `owner` 仅作标签不鉴权，与 `agent_prompt` 不同——任务不是知识资产，生命周期以 `cancelled` 终态表达。

---

## 子命令

### init — 建表与自检

```bash
python -m baibao pt init
```

幂等建表 + 自检，打印任务数。

### create — 建任务

```bash
# 从文件读目标
python -m baibao pt create --title "补全 th-core-server 文档" \
  --goal-file goals/doc-completion.txt --created-by zhangsan

# 从 stdin 读目标
python -m baibao pt create --title "数据迁移" --goal-file - <<'EOF'
把 legacy 库的 user 表迁移到新库，字段映射见 docs/mapping.md
EOF

# 带参数 + 自定义重试预算 + 超时
python -m baibao pt create --title "批量 OCR" --goal-file - \
  --params-json '{"batch_size": 100}' --max-retries 3 --timeout-sec 7200
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--title` | str | 是 | - | 任务标题 |
| `--goal` | str | 二选一 | - | 任务目标内联；与 `--goal-file` 互斥 |
| `--goal-file` | str | 二选一 | - | 任务目标文件；`-` 读 stdin |
| `--template` | str | 否 | - | 来源模板名 |
| `--params-json` | str | 否 | - | 任务参数 JSON 内联；与 `--params-file` 互斥 |
| `--params-file` | str | 否 | - | 任务参数 JSON 文件；`-` 读 stdin |
| `--parent` | int | 否 | - | 父任务 id（父子任务） |
| `--max-retries` | int | 否 | 1 | 步骤默认重试预算（步骤未指定时继承） |
| `--timeout-sec` | int | 否 | - | 任务总超时秒；不设则不限 |
| `--heartbeat-timeout-sec` | int | 否 | 1800 | 心跳超时阈值秒，超过判僵尸 |
| `--created-by` | str | 否 | 环境变量/配置 | 创建者标签 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

输出 task_id。

### plan — 批量导入步骤

```bash
python -m baibao pt plan 1 --steps-file steps.json
python -m baibao pt plan 1 --steps-file - <<'EOF'    # stdin
[
  {"name": "调研", "instruction": "分析现有代码结构", "step_type": "agent"},
  {"name": "写文档", "instruction": "根据调研结果写技术文档", "step_type": "agent", "timeout_sec": 1800, "max_retries": 2},
  {"name": "跑测试", "instruction": "执行 pytest 验证", "step_type": "bash"}
]
EOF
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--steps-file` | str | 是 | - | 步骤 JSON 数组文件；`-` 读 stdin |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

步骤数组元素字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | str | 是 | - | 步骤名 |
| `instruction` | str | 是 | - | 该步骤完整指令（prompt/命令） |
| `step_type` | str | 否 | `agent` | `agent` / `bash` / `human_approval` / `condition` |
| `timeout_sec` | int | 否 | - | 单步超时秒；不设则不限 |
| `max_retries` | int | 否 | 继承任务 | 最大重试次数（含首次共 max_retries+1 次机会） |

`instruction` 支持嵌 `@文件路径` 引用外部文件内容。

### step add — 单条加步骤

```bash
python -m baibao pt step add 1 --name "补充测试" \
  --instruction-file instructions/test.md --step-type agent --max-retries 2
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--name` | str | 是 | - | 步骤名 |
| `--instruction` | str | 二选一 | - | 指令内联；与 `--instruction-file` 互斥 |
| `--instruction-file` | str | 二选一 | - | 指令文件；`-` 读 stdin |
| `--step-type` | 枚举 | 否 | `agent` | `agent` / `bash` / `human_approval` / `condition` |
| `--timeout-sec` | int | 否 | - | 单步超时秒 |
| `--max-retries` | int | 否 | 继承任务 | 最大重试次数 |

`seq` 缺省自动 `max+1`。

### claim — 原子认领下一步骤

```bash
python -m baibao pt claim 1
python -m baibao pt claim 1 --session-id session-002 --agent-name zcode-recovery
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--session-id` | str | 否 | 环境变量/配置 | 执行会话标识 |
| `--agent-name` | str | 否 | 环境变量/配置 | agent 外壳标识 |
| `--format` | 枚举 | 否 | `jsonl` | 仅 `json` / `jsonl` |

返回续跑上下文包（一个 dict），这是新会话接手所需的全部信息：

```json
{
  "task":  {"id": 1, "title": "...", "goal": "...", "params": {}, "status": "running"},
  "step":  {"id": 101, "seq": 3, "name": "...", "step_type": "agent",
            "instruction": "...", "retry_count": 1, "timeout_sec": 1800},
  "run_id": 9001,
  "context": [
    {"seq": 1, "name": "...", "result_summary": "..."},
    {"seq": 2, "name": "...", "result_summary": "..."}
  ]
}
```

任务无 pending 步骤（或任务非 pending/running）返回 `None`——只说明无事可做，用 `status` 查终态即可。AI 拿到续跑上下文包即可直接派子代理，不需要再拼查询。

### finish — 成功收口

```bash
python -m baibao pt finish 9001 --output-file result.txt --summary "文档已补全 5 个模块"
python -m baibao pt finish 9001 --output "done" --summary "迁移完成" --token-usage 3200
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `run_id` | int | 是 | - | 执行 id（位置参数） |
| `--output` | str | 否 | - | 执行输出内联；与 `--output-file` 互斥 |
| `--output-file` | str | 否 | - | 执行输出文件；`-` 读 stdin |
| `--summary` | str | 否 | - | 结果摘要（续跑会话的上下文来源，比 output 更重要）；与 `--summary-file` 互斥 |
| `--summary-file` | str | 否 | - | 结果摘要文件；`-` 读 stdin |
| `--token-usage` | int | 否 | - | 本次 token 消耗 |
| `--format` | 枚举 | 否 | `jsonl` | 仅 `json` / `jsonl` |

成功后 stdout 回显步骤状态。`summary` 缺省时截取 output 前 2000 字。全部步骤成功后任务自动置 `completed`。

> **finish 必须带 summary**（一句话结果），它是后续步骤的上下文，比 output 更重要。

### fail — 失败上报

```bash
python -m baibao pt fail 9001 --error "子代理超时未返回"
python -m baibao pt fail 9001 --error-file error.log
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `run_id` | int | 是 | - | 执行 id（位置参数） |
| `--error` | str | 二选一 | - | 失败原因内联；与 `--error-file` 互斥 |
| `--error-file` | str | 二选一 | - | 失败原因文件；`-` 读 stdin |

自动按重试预算决定去向：预算未耗尽 → step 回 `pending`（retry_count+1），返回 `retried`；预算耗尽 → step 置 `failed` + 任务置 `failed`，返回 `step_failed`。

### heartbeat — 刷心跳

```bash
python -m baibao pt heartbeat 1
```

约定 claim / finish / fail / 任何写操作都顺带刷心跳——活动即心跳，避免编排层忘刷。执行长指令期间可穿插手动刷。

### status — 任务总览

```bash
python -m baibao pt status 1
python -m baibao pt status 1 --full    # 看全文（goal/instruction/output）
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--snippet` | int | 否 | 300 | 长字段预览截断字数 |
| `--full` | 开关 | 否 | 关 | 输出长字段全文 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

输出任务信息 + 步骤列表 + 动态进度（done/total）+ 产物计数。

### list — 任务列表

```bash
python -m baibao pt list --status running
python -m baibao pt list --created-by zhangsan --limit 20
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--status` | str | 否 | - | 按状态过滤：pending/running/paused/completed/failed/cancelled |
| `--created-by` | str | 否 | - | 按创建者过滤 |
| `--limit` | int | 否 | 50 | 返回条数 |
| `--snippet` | int | 否 | 300 | 长字段预览截断字数 |
| `--full` | 开关 | 否 | 关 | 输出长字段全文 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

### pause / resume / cancel — 生命周期控制

```bash
python -m baibao pt pause 1       # 不派发新步骤，不强杀 running 步骤
python -m baibao pt resume 1
python -m baibao pt cancel 1 --reason "需求变更"
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--reason` | str | 否 | `""` | 仅 cancel；取消原因 |

`cancel` 把 running 步骤连带置 `failed`、run 置 `cancelled`。

### retry / skip — 步骤操作

```bash
python -m baibao pt retry 101      # 手动给失败步骤再一次机会（预算 +1，回 pending）
python -m baibao pt skip 102 --reason "该步骤不再需要"
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `step_id` | int | 是 | - | 步骤 id（位置参数） |
| `--reason` | str | 否 | `""` | 仅 skip；跳过原因 |

`retry`：校验预算 +1 后回 pending；任务若因该步骤 failed 则复活回 running。
`skip`：仅 pending 可 skip；跳过最后剩余步骤时触发任务自动收口 completed。

### sweep — 僵尸检测与恢复

```bash
python -m baibao pt sweep                           # 逐任务用各自心跳配置
python -m baibao pt sweep --heartbeat-timeout-sec 600   # 全局覆盖心跳阈值
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--heartbeat-timeout-sec` | int | 否 | - | 全局覆盖心跳超时秒；不设则逐任务用自身配置 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

幂等，可重复执行。检测三类超时：

| 类型 | 判定 | 动作 |
|------|------|------|
| 任务总超时 | running 且 `timeout_sec` 非空且 `started_at` 距今超过它 | running 步骤直接 failed（不走预算），任务 failed |
| 任务心跳超时 | running 且 `heartbeat_at` 超过阈值 | run 置 timeout，步骤按预算回 pending 或 failed；任务保持 running（心跳断了≠任务死了） |
| 单步超时 | run.started_at 超过 step.timeout_sec 但仍在 running | run 置 timeout，同上按预算处理 |

返回被恢复对象摘要列表，每项 `{task_id, step_id, run_id, action, detail}`，action 取值 `run_timeout` / `step_retry` / `step_failed` / `task_timeout` / `task_failed`。

### artifact — 产物登记与查询

```bash
# 登记
python -m baibao pt artifact add 1 --path docs/completion-report.md --type report --step 101
python -m baibao pt artifact add 1 --path logs/run.log --type log --note "执行日志"

# 查询
python -m baibao pt artifact list 1
```

`artifact add` 选项：

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--path` | str | 是 | - | 产物路径（相对仓库根或绝对路径） |
| `--type` | 枚举 | 否 | `file` | `file` / `report` / `diff` / `log` / `other` |
| `--step` | int | 否 | - | 所属步骤 id；不设为任务级产物 |
| `--note` | str | 否 | - | 备注 |

### event list — 事件流水查询

```bash
python -m baibao pt event list 1 --limit 50
```

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--limit` | int | 否 | 100 | 返回条数 |
| `--format` | 枚举 | 否 | `jsonl` | `json` / `jsonl` / `csv` / `table` |

事件是 append-only 流水账，每次状态流转自动留痕（event_type='state_change'，如 `step 12: pending → running (claim by session-xxx)`）。状态对不上时以事件流水为准。

### template — 模板存取

```bash
# 把任务步骤存为模板蓝图
python -m baibao pt template save 1 --name "doc-completion-template" --description "文档补全任务模板"

# 列模板
python -m baibao pt template list
```

`template save` 选项：

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_id` | int | 是 | - | 任务 id（位置参数） |
| `--name` | str | 是 | - | 模板名（唯一） |
| `--description` | str | 否 | - | 模板说明 |
| `--skill-ref` | str | 否 | - | 关联的技能标识 |

---

## 输出

- **默认格式 `jsonl`**（一行一对象，省 token）；`--format json` 缩进多行便于人读，`csv` / `table` 用于浏览。
- `claim` / `finish` 仅支持 `json` / `jsonl`（续跑上下文包和回显状态用单对象输出）。
- 长字段（goal/instruction/output）默认 300 字 snippet 预览（折叠换行 + 截断 + `…（+N字，--full 看全文）` 指引），`--full` 输出全文，`--snippet N` 调整截断长度。给 AI 看的默认输出省 token。
- 退出码：`0` 成功；`1` 参数错误 / 任务不存在 / 鉴权失败 / 超时。
- 命令返回 bool；失败时 stderr 输出可行动提示（如 `claim` 返回空时提示"任务已无 pending 步骤，用 status 看终态"）。

---

## 已知坑与版本兼容

| 现象 | 根因 | 解法 |
|------|------|------|
| `claim` 返回空 | 任务无 pending 步骤（全部 succeeded/skipped 或任务非 pending/running） | 用 `status` 查终态；若步骤 failed 用 `retry` 复活 |
| 步骤 failed 后任务卡住 | 预算耗尽，步骤终态 failed 连带任务 failed | `pt retry <step_id>` 手动给一次机会（预算 +1，任务回 running），再 `claim` |
| 任务长时间 running 无进展 | 会话崩溃 / 断连，run 停在 running 成僵尸 | `pt sweep` 清僵尸：超时 run 置 timeout，步骤按预算回 pending；再 `claim` 续跑 |
| `plan` 的 instruction 里 `@文件路径` 没生效 | 路径写错或文件不存在 | 用相对仓库根的路径；`@` 后紧跟路径无空格 |
| Windows shell 引号剥离长 instruction/goal | argv 引号处理问题 | 用 `--xxx-file` / `-` 读 stdin，不内联长文本 |
| 并发 `step add` 撞唯一键 | `seq = max(seq)+1` 取号在多进程并发下可能撞 | 本期 CLI 单进程使用，可接受；撞键时报错重试 |

### 良性告警

| 告警 | 含义 | 是否需处理 |
|------|------|-----------|
| `PLAN_TASK_OWNER 未配置` | created_by 标签为空 | 否（仅标签，不鉴权；需要追溯归属时再配） |

### 版本兼容

| 组件 | 要求 | 说明 |
|------|------|------|
| 数据库 | **仅 MySQL** | 非 MySQL 实例直接报错拒绝（PostgreSQL/SQLite 方言本期不支持） |
| Python | ≥ 3.8 | 依赖 pykunlun / baibao |
| MySQL | ≥ 5.7 | 使用 JSON 列类型 |
| rdb 实例 | 复用 `ai_agent` 可写实例 | 与 `agent_prompt` / `agent_memory` 同库；建议单独注册别名隔离 |

---

## 完整示例

一个断点可续的计划任务完整流程（建任务 → 拆步骤 → 执行循环 → 崩溃后恢复）：

```bash
# 0) 首次初始化
python -m baibao pt init

# 1) 建任务 + 拆步骤
python -m baibao pt create --title "补全 th-core-server 文档" --goal-file - <<'EOF'
分析 th-core-server 代码，补全缺失的技术文档，覆盖 controller/service/mapper 三层
EOF
# 输出 task_id: 1

python -m baibao pt plan 1 --steps-file - <<'EOF'
[
  {"name": "调研", "instruction": "分析现有代码结构与文档缺口", "step_type": "agent"},
  {"name": "写文档", "instruction": "根据调研结果写技术文档", "step_type": "agent", "max_retries": 2},
  {"name": "跑测试", "instruction": "pytest 验证文档示例可运行", "step_type": "bash"}
]
EOF

# 2) 主循环（编排层：主会话轻上下文）
python -m baibao pt claim 1
# → 续跑上下文包（step + run_id + 前序摘要）
#   ↓ 派子代理执行 instruction（执行层：子代理全新上下文）

python -m baibao pt finish <run_id> --output-file - --summary "调研完成，发现 5 个模块缺文档" <<'EOF'
controller 层 3 个、service 层 1 个、mapper 层 1 个模块文档缺失
EOF
# 或失败：python -m baibao pt fail <run_id> --error "子代理超时"

# 循环直到 claim 返回空

# 3) 收尾
python -m baibao pt status 1                    # completed
python -m baibao pt artifact list 1             # 看产物

# 4) 崩溃后（新会话接手，或 cron 唤醒）
python -m baibao pt sweep                       # 清僵尸：超时 run 置 timeout，步骤按预算回 pending
python -m baibao pt list --status running       # 找到未完任务
python -m baibao pt claim 1                     # 从断点继续——前序成果在 context 里
```

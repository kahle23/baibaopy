"""
plan_task 命令 - AI 计划任务的高层操作（create / plan / claim / finish / fail 等）。

基于 :mod:`baibao.task.plan` 的 ``MySqlPlanTaskService``，对外封装为面向
AI/用户的高层子命令。数据库是唯一真相源，AI 会话只是可随时替换的执行单元——
断了就 ``sweep → list --status running → claim`` 从断点续跑。

子命令概览：
  - init        幂等建 ai_task_* 五张表 + 自检
  - create      建任务，输出 task_id
  - plan        批量导入步骤（JSON 数组，stdin/文件，instruction 支持 @文件 引用）
  - step add    单条加步骤
  - claim       原子认领下一步骤，输出续跑上下文包（新会话接手所需的全部信息）
  - finish      成功收口一次执行（--output-file/--summary-file 支持 "-" 读 stdin；
                建议必带 --summary；成功后 stdout 回显更新后的步骤状态）
  - fail        失败上报（自动按重试预算决定回 pending 重试或终败）
  - heartbeat   刷任务心跳
  - status      任务总览（任务+步骤+进度+产物计数）
  - list        任务列表
  - pause/resume/cancel   生命周期控制
  - retry/skip  步骤手动重置/跳过
  - sweep       僵尸检测与恢复（幂等）
  - artifact    产物登记/查询
  - event       事件流水查询

身份与配置：
  - 配置文件 ``.baibao/plan_task.config``（当前目录优先，再用户目录，utf-8-sig 容错），
    只放环境事实键：``rdb_name`` / ``owner``；
  - ``session_id`` / ``agent_name`` 是调用者身份，不进配置文件，仅经命令行标志
    ``--session-id`` / ``--agent-name`` 或环境变量 ``PLAN_TASK_SESSION_ID`` /
    ``PLAN_TASK_AGENT_NAME`` 注入（不传则 run 行落 NULL）；
  - 环境变量 ``PLAN_TASK_DB`` / ``PLAN_TASK_OWNER`` 同理覆盖配置文件；解析优先级：
    命令行标志 > 环境变量 > 配置文件；
  - ``owner`` 仅作为 created_by 标签（不鉴权），未配置只告警不阻断。

长文本（goal/instruction/output/error/steps/params）一律提供 ``--xxx-file`` 且支持
"-" 读 stdin——绕开 Windows shell argv 引号剥离问题。默认输出省 token：长字段
（goal/instruction/output 等）折叠为 300 字预览，``--full`` 输出全文。
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, datetime
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.task.plan import (
    VALID_ART_TYPES,
    VALID_STEP_TYPES,
    PlanTask,
    PlanTaskManager,
    TaskStep,
)
from pykunlun.util import logutil

from baibao.task.plan import MySqlPlanTaskService

log = logutil.getLogger(__name__)

#: 身份配置文件名
_CONFIG_FILENAMES = ('plan_task.config',)

#: 配置缓存（一次进程内复用）
_config_cache: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    """搜索并加载配置文件，返回字典（无则为空 dict）。优先级：先找到的为准。

    候选目录为 当前目录 → 用户目录，各目录下探测 ``.baibao/plan_task.config``。
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    dirs = [os.getcwd(), os.path.expanduser('~')]
    paths = [os.path.join(d, '.baibao', fn) for d in dirs for fn in _CONFIG_FILENAMES]
    for p in paths:
        try:
            if os.path.isfile(p):
                # utf-8-sig 容忍编辑器写入的 BOM
                with open(p, encoding='utf-8-sig') as f:
                    data: dict[str, Any] = json.load(f)
                if isinstance(data, dict):
                    log.debug("加载计划任务配置: %s", p)
                    _config_cache = data
                    return data
        except Exception:
            log.warning("读取计划任务配置失败: %s", p, exc_info=True)
    _config_cache = {}
    return _config_cache


def _read_text_source(path: str | None) -> str | None:
    """从文件或 stdin 读取长文本，绕开命令行 shell 引号转义问题。

    - ``path`` 为 None/空：返回 None（调用方按原参数处理）。
    - ``path == '-'``：读取 stdin 全文。
    - 其它：按 UTF-8（容忍 BOM）读取文件全文，原样返回（不去除换行）。
    """
    if not path:
        return None
    if path == '-':
        return sys.stdin.read()
    with open(path, encoding='utf-8-sig') as f:
        return f.read()


def _expand_at_file(instruction: str) -> str:
    """展开 instruction 的 ``@文件路径`` 引用（整值以 ``@`` 开头时替换为文件内容）。

    步骤指令往往很长（完整 prompt），不适合塞进 JSON 数组走 argv——plan 的 steps
    JSON 里写 ``"instruction": "@docs/step1_prompt.md"``，由此在落库前替换为文件全文。
    """
    if instruction.startswith('@') and len(instruction) > 1:
        path = instruction[1:].strip()
        with open(path, encoding='utf-8-sig') as f:
            return f.read()
    return instruction


class _CustomEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，处理日期时间类型。"""

    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


class PlanTaskCommand(Command):
    """
    AI 计划任务命令（断点可续、重试有据、全程留痕）。

    每次调用按配置（环境变量/配置文件）解析 rdb 实例名，构造
    :class:`MySqlPlanTaskService` 并经 :class:`PlanTaskManager` 转发。
    """

    @property
    def name(self) -> str:
        return 'plan_task'

    @property
    def abbr(self) -> str:
        return 'pt'

    @property
    def description(self) -> str:
        return 'AI 计划任务操作（建任务/拆步骤/认领/收口/断点续跑）'

    @property
    def usage(self) -> str:
        return (
            "python -m baibao plan_task <子命令> [选项]\n"
            "python -m baibao pt <子命令> [选项]        # 缩写\n"
            "\n"
            "生命周期:  init → create → plan → (claim → finish|fail)* → status\n"
            "           bash 步骤可无人值守：run = claim + 执行 + 自动收口（循环到无可认领）\n"
            "恢复入口:  sweep → list --status running → claim（断点续跑）\n"
            "\n"
            "子命令:\n"
            "  init                                        幂等建 5 张 ai_task_* 表 + 自检\n"
            "  create  --title --goal/--goal-file [...]    建任务，输出 task_id\n"
            "  plan    <task_id> --steps-file FILE         批量导入步骤（JSON 数组，'-' 读 stdin；\n"
            "                                              instruction 支持 @文件路径 引用）\n"
            "  step add <task_id> --name --instruction/--instruction-file  单条加步骤\n"
            "  claim   <task_id> [--session-id --agent-name --format]  原子认领下一步骤，输出续跑上下文包\n"
            "                                              （依赖感知：声明了 depends_on 的任务只认领依赖就绪者；\n"
            "                                               --ignore-deps 无视依赖按 seq 最小；--format: jsonl|json）\n"
            "  run     <task_id> [--session-id --agent-name --format]  无人值守执行：循环认领并自动跑 bash 步骤\n"
            "                                              （subprocess 执行 instruction、超时强杀进程树、\n"
            "                                               自动 finish/fail；agent 等非 bash 步骤 release 并停止；\n"
            "                                               失败预算内回 pending 但不自动重跑，重跑即续）\n"
            "  finish  <run_id> [--output/--output-file --summary/--summary-file]  成功收口\n"
            "                                              （建议必带 --summary；--token-usage 可选回填；\n"
            "                                               成功后 stdout 回显步骤状态）\n"
            "  fail    <run_id> --error/--error-file       失败上报（按预算自动重试/终败）\n"
            "  release <run_id> [--reason]                 释放认领（run→cancelled、步骤回 pending，\n"
            "                                              不消耗重试预算；会话结束/派发放弃用）\n"
            "  heartbeat <task_id>                         刷心跳\n"
            "  status  <task_id> [--full]                  任务总览（任务+步骤+进度+产物计数）\n"
            "  list    [--status --created-by --limit]     任务列表\n"
            "  pause / resume / cancel <task_id>           生命周期控制（cancel 带 --reason）\n"
            "  retry   <step_id> [--force]                 手动重置失败步骤回 pending（预算+1）；\n"
            "                                              --force 加放 succeeded（清假摘要、completed\n"
            "                                              任务一并复活）——管理修复用\n"
            "  skip    <step_id> [--reason]                跳过 pending 步骤\n"
            "  sweep   [--heartbeat-timeout-sec N]         僵尸检测与恢复（幂等；任务总超时/\n"
            "                                              心跳超时/单步超时三类）\n"
            "  verify  <task_id> [--fix]                   一致性对账（按事件流水/run 核对步骤状态，\n"
            "                                              识别绕过状态机的直接改库；--fix 就地修复）\n"
            "  artifact add <task_id> --path [--type --step --note]  产物登记\n"
            "  artifact list <task_id>                     产物查询\n"
            "  event list <task_id> [--limit]              事件流水查询\n"
            "\n"
            "配置: .baibao/plan_task.config（只放环境事实键 rdb_name/owner）\n"
            "      环境变量: PLAN_TASK_DB / PLAN_TASK_OWNER\n"
            "身份: --session-id / --agent-name 标志或 PLAN_TASK_SESSION_ID / PLAN_TASK_AGENT_NAME\n"
            "      环境变量（调用者身份，不进配置文件；不传则 run 行落 NULL）\n"
            "\n"
            "通用选项:\n"
            "  --format FMT  输出格式: json|jsonl|csv|table（默认: jsonl）\n"
            "  -h, --help    显示帮助信息"
        )

    def execute(self, ctx: CliContext) -> Any:
        args = ctx.current_args
        if not args:
            self.show_usage()
            return False

        sub = args[0]
        rest = args[1:]
        try:
            if sub == 'init':
                return self._init(ctx, rest)
            if sub == 'create':
                return self._create(ctx, rest)
            if sub == 'plan':
                return self._plan(ctx, rest)
            if sub == 'step':
                return self._step(ctx, rest)
            if sub == 'claim':
                return self._claim(ctx, rest)
            if sub == 'run':
                return self._run(ctx, rest)
            if sub == 'finish':
                return self._finish(ctx, rest)
            if sub == 'fail':
                return self._fail(ctx, rest)
            if sub == 'release':
                return self._release(ctx, rest)
            if sub == 'heartbeat':
                return self._heartbeat(ctx, rest)
            if sub == 'status':
                return self._status(ctx, rest)
            if sub == 'list':
                return self._list(ctx, rest)
            if sub == 'pause':
                return self._pause(ctx, rest)
            if sub == 'resume':
                return self._resume(ctx, rest)
            if sub == 'cancel':
                return self._cancel(ctx, rest)
            if sub == 'retry':
                return self._retry(ctx, rest)
            if sub == 'skip':
                return self._skip(ctx, rest)
            if sub == 'sweep':
                return self._sweep(ctx, rest)
            if sub == 'verify':
                return self._verify(ctx, rest)
            if sub == 'artifact':
                return self._artifact(ctx, rest)
            if sub == 'event':
                return self._event(ctx, rest)
            if sub in ('-h', '--help'):
                self.show_usage()
                return True
            log.error(f"未知子命令: {sub}")
            self.show_usage()
            return False
        except Exception as e:
            log.error(f"plan_task {sub} 失败: {e}")
            return False

    # region ======== 工具：配置解析与构造 ========
    @staticmethod
    def _build_mgr() -> PlanTaskManager:
        """构造 PlanTaskManager（rdb 实例名走配置，调用方无需关心）。"""
        cfg = _load_config()
        db_name = (os.environ.get('PLAN_TASK_DB')
                   or cfg.get('rdb_name'))
        mgr = PlanTaskManager()
        mgr.register(PlanTaskManager.DEFAULT_NAME, MySqlPlanTaskService(db_name=db_name))
        return mgr

    @staticmethod
    def _resolve_created_by(ns: argparse.Namespace) -> str | None:
        """解析 created_by 标签：标志 > PLAN_TASK_OWNER > 配置文件；未配置仅告警。"""
        cfg = _load_config()
        created_by = (getattr(ns, 'created_by', None)
                      or os.environ.get('PLAN_TASK_OWNER')
                      or cfg.get('owner'))
        if created_by is None:
            log.info("未配置 owner（PLAN_TASK_OWNER / plan_task.config），任务将无创建者标签；"
                     "多机共用库建议配置以便筛选")
        return created_by

    @staticmethod
    def _resolve_session(ns: argparse.Namespace) -> tuple[str | None, str | None]:
        """解析 (session_id, agent_name)：标志 > PLAN_TASK_* 环境变量（调用者身份，不读配置文件）。"""
        session_id = (getattr(ns, 'session_id', None)
                      or os.environ.get('PLAN_TASK_SESSION_ID'))
        agent_name = (getattr(ns, 'agent_name', None)
                      or os.environ.get('PLAN_TASK_AGENT_NAME'))
        return session_id, agent_name

    def _emit(self, ctx: CliContext, rows: list[dict[str, Any]], fmt: str) -> None:
        ctx.print_delim()
        self._format_result(rows, fmt)
        ctx.print_delim()

    def _emit_one(self, ctx: CliContext, row: dict[str, Any], fmt: str) -> None:
        """输出单个对象（非列表，如续跑上下文包）为一段 JSON。

        ``fmt='jsonl'`` 单行（省 token，可整体 ``json.loads`` 解析）；
        ``'json'`` 缩进多行（人读友好）。两种格式都不可逐行 ``json.loads`` 多行对象的某一行。
        """
        ctx.print_delim()
        if fmt == 'jsonl':
            print(json.dumps(row, ensure_ascii=False, cls=_CustomEncoder))
        else:
            print(json.dumps(row, ensure_ascii=False, indent=2, cls=_CustomEncoder))
        ctx.print_delim()

    def _format_result(self, rows: list[dict[str, Any]], fmt: str) -> None:
        if not rows:
            log.info("结果为空")
            return
        if fmt == 'json':
            print(json.dumps(rows, ensure_ascii=False, indent=2, cls=_CustomEncoder))
        elif fmt == 'csv':
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
            print(output.getvalue(), end='')
        elif fmt == 'table':
            self._print_table(rows)
        else:  # jsonl
            for row in rows:
                print(json.dumps(row, ensure_ascii=False, cls=_CustomEncoder))

    @staticmethod
    def _print_table(rows: list[dict[str, Any]]) -> None:
        columns = list(rows[0].keys())
        widths = {c: max(len(str(c)), max(len(str(r.get(c, ''))) for r in rows)) for c in columns}
        header = ' | '.join(str(c).ljust(widths[c]) for c in columns)
        print(header)
        print('-' * len(header))
        for r in rows:
            print(' | '.join(str(r.get(c, '')).ljust(widths[c]) for c in columns))
        print(f"\n共 {len(rows)} 条记录")

    @staticmethod
    def _apply_snippet(rows: list[dict[str, Any]], limit: int,
                       fields: tuple[str, ...] = ('goal',)) -> list[dict[str, Any]]:
        """对 rows 的指定长字段做预览截断（省 AI 上下文）。

        折叠空白（含换行）为单行预览；超 limit 则截断并追加 ``…（+N 字，--full 看全文）``。
        """
        for r in rows:
            for fld in fields:
                v = r.get(fld)
                if not isinstance(v, str):
                    continue
                full_len = len(v)
                preview = ' '.join(v[:limit].split())
                if full_len > limit:
                    preview = f'{preview}…（+{full_len - limit} 字，--full 看全文）'
                r[fld] = preview
        return rows
    # endregion

    # region ======== 子命令实现：初始化 / 建任务 / 拆步骤 ========
    def _init(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task init')
        parser.parse_args(args)
        mgr = self._build_mgr()
        mgr.setup()
        n = len(mgr.list_tasks(limit=1000))
        ctx.print_delim()
        print(f"计划任务库已就绪（5 张 ai_task_* 表，service=mysql），现有任务 {n} 个")
        ctx.print_delim()
        return True

    def _create(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task create')
        parser.add_argument('--title', required=True, help='任务标题')
        goal_group = parser.add_mutually_exclusive_group(required=True)
        goal_group.add_argument('--goal', default=None,
                                help='任务目标（与 --goal-file 二选一）')
        goal_group.add_argument('--goal-file', dest='goal_file', default=None,
                                help='从 UTF-8 文件读取任务目标；"-" 读 stdin'
                                     '（适合含引号/特殊字符/超长内容）')
        parser.add_argument('--source', type=int, default=None,
                            help='来源标识 id（上层自定义，如 prompt 库/技能侧的内部 id；'
                                 '仅盖章落库）')
        params_group = parser.add_mutually_exclusive_group(required=False)
        params_group.add_argument('--params-json', default=None,
                                  help='任务参数 JSON 对象（与 --params-file 二选一）')
        params_group.add_argument('--params-file', dest='params_file', default=None,
                                  help='从 UTF-8 文件读取任务参数 JSON；"-" 读 stdin')
        parser.add_argument('--parent', type=int, default=None, help='父任务 id')
        parser.add_argument('--max-retries', type=int, default=None, dest='max_retries',
                            help='步骤默认重试预算（默认 1）')
        parser.add_argument('--timeout-sec', type=int, default=None, dest='timeout_sec',
                            help='任务总超时秒（默认不限）')
        parser.add_argument('--heartbeat-timeout-sec', type=int, default=None,
                            dest='heartbeat_timeout_sec', help='心跳超时阈值秒（默认 1800）')
        parser.add_argument('--created-by', default=None, dest='created_by',
                            help='创建者标签（默认: 环境变量 PLAN_TASK_OWNER > 配置 owner）')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args)

        goal = ns.goal if ns.goal is not None else _read_text_source(ns.goal_file)
        assert goal is not None
        params: dict[str, Any] | None = None
        if ns.params_json is not None:
            params = json.loads(ns.params_json)
        elif ns.params_file is not None:
            params = json.loads(_read_text_source(ns.params_file) or 'null')

        created_by = self._resolve_created_by(ns)
        mgr = self._build_mgr()

        task = PlanTask(title=ns.title, goal=goal, source_id=ns.source,
                        parent_task_id=ns.parent, params=params,
                        max_retries=ns.max_retries if ns.max_retries is not None else 1,
                        heartbeat_timeout_sec=(ns.heartbeat_timeout_sec
                                               if ns.heartbeat_timeout_sec is not None
                                               else 1800),
                        timeout_sec=ns.timeout_sec, created_by=created_by)
        task_id = mgr.create_task(task)
        log.info("任务已创建 id=%s (title=%s, created_by=%s, source=%s)",
                 task_id, ns.title, created_by, ns.source)
        task = mgr.get_task(task_id)
        if task is not None:
            self._emit(ctx, self._apply_snippet([task.to_dict()], 300), ns.format)
        return True

    def _plan(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task plan')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--steps-file', required=True, dest='steps_file',
                            help='步骤 JSON 数组文件（"-" 读 stdin）；元素 '
                                 '{name, instruction, step_type, timeout_sec, max_retries, '
                                 'depends_on}，instruction 支持 @文件路径 引用；'
                                 'depends_on 为依赖步骤的 seq 数组（claim 依赖感知依据）')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args)

        text = _read_text_source(ns.steps_file)
        assert text is not None
        try:
            raw_steps: list[dict[str, Any]] = json.loads(text)
        except json.JSONDecodeError as e:
            log.error("steps JSON 解析失败: %s", e)
            return False
        if not isinstance(raw_steps, list) or not raw_steps:
            log.error("steps 须为非空 JSON 数组")
            return False
        steps: list[TaskStep] = []
        for i, item in enumerate(raw_steps):
            if not isinstance(item, dict) or not item.get('name') or not item.get('instruction'):
                log.error("第 %d 个步骤缺少 name/instruction", i + 1)
                return False
            steps.append(TaskStep(
                task_id=ns.task_id,
                name=str(item['name']),
                instruction=_expand_at_file(str(item['instruction'])),
                step_type=str(item.get('step_type') or 'agent'),
                timeout_sec=item.get('timeout_sec'),
                max_retries=item.get('max_retries'),
                depends_on=item.get('depends_on'),
            ))
        n = self._build_mgr().add_steps(steps)
        log.info("已导入 %d 个步骤（task=%s，seq %d..%d）", n, ns.task_id,
                 steps[0].seq or 0, steps[-1].seq or 0)
        rows = self._build_mgr().list_steps(ns.task_id)
        self._emit(ctx, self._apply_snippet(rows, 300, fields=('instruction',)), ns.format)
        return True

    def _step(self, ctx: CliContext, args: list[str]) -> bool:
        if not args:
            log.error("step 需要二级子命令: add")
            return False
        sub, rest = args[0], args[1:]
        if sub == 'add':
            return self._step_add(ctx, rest)
        log.error(f"未知二级子命令: step {sub}")
        return False

    def _step_add(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task step add')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--name', required=True, help='步骤名')
        instr_group = parser.add_mutually_exclusive_group(required=True)
        instr_group.add_argument('--instruction', default=None,
                                 help='该步骤完整指令（与 --instruction-file 二选一）')
        instr_group.add_argument('--instruction-file', dest='instruction_file', default=None,
                                 help='从 UTF-8 文件读取指令；"-" 读 stdin')
        parser.add_argument('--step-type', default='agent', dest='step_type',
                            choices=sorted(VALID_STEP_TYPES),
                            help='步骤类型（默认 agent）')
        parser.add_argument('--timeout-sec', type=int, default=None, dest='timeout_sec',
                            help='单步超时秒（默认不限）')
        parser.add_argument('--max-retries', type=int, default=None, dest='max_retries',
                            help='最大重试次数（默认继承任务级）')
        parser.add_argument('--depends-on', default=None, dest='depends_on',
                            help='依赖步骤的 seq，逗号分隔（如 "1,3"）；'
                                 'claim 依赖感知模式下依赖未就绪不会被认领')
        ns = parser.parse_args(args)

        instruction = (ns.instruction if ns.instruction is not None
                       else _read_text_source(ns.instruction_file))
        assert instruction is not None
        depends_on: list[int] | None = None
        if ns.depends_on:
            try:
                depends_on = [int(x) for x in str(ns.depends_on).split(',') if x.strip()]
            except ValueError:
                log.error("--depends-on 须为逗号分隔的整数 seq（如 \"1,3\"）")
                return False
        step = TaskStep(task_id=ns.task_id, name=ns.name, instruction=instruction,
                        step_type=ns.step_type, timeout_sec=ns.timeout_sec,
                        max_retries=ns.max_retries, depends_on=depends_on)
        step_id = self._build_mgr().add_step(step)
        log.info("步骤已添加 id=%s (task=%s, seq=%s, name=%s)",
                 step_id, ns.task_id, step.seq, ns.name)
        return True
    # endregion

    # region ======== 子命令实现：执行主循环 ========
    def _claim(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task claim')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--session-id', default=None, dest='session_id',
                            help='执行会话标识（默认: 环境变量 PLAN_TASK_SESSION_ID，不传则不落库）')
        parser.add_argument('--agent-name', default=None, dest='agent_name',
                            help='agent 外壳标识（默认: 环境变量 PLAN_TASK_AGENT_NAME，不传则不落库）')
        parser.add_argument('--format', dest='format', choices=['json', 'jsonl'], default='jsonl',
                            help='续跑上下文包输出格式（默认: jsonl 单行省 token；json 为缩进多行）')
        parser.add_argument('--ignore-deps', action='store_true', dest='ignore_deps',
                            help='无视 depends_on 依赖声明，按旧行为认领 seq 最小 pending（逃生开关）')
        ns = parser.parse_args(args)

        session_id, agent_name = self._resolve_session(ns)
        package = self._build_mgr().claim_next_step(ns.task_id, session_id=session_id,
                                                    agent_name=agent_name,
                                                    ignore_deps=ns.ignore_deps)
        if package is None:
            log.info("任务 %s 已无可认领步骤（无 pending / 依赖均未就绪 / 非 pending-running 状态）；"
                     "用 status %s 看终态", ns.task_id, ns.task_id)
            return True
        log.info("已认领 step %s (run_id=%s)，续跑上下文包如下（前序 %d 步摘要）",
                 package['step']['id'], package['run_id'], len(package['context']))
        self._emit_one(ctx, package, ns.format)
        return True

    def _run(self, ctx: CliContext, args: list[str]) -> bool:
        """无人值守执行：循环认领 bash 步骤并自动收口（run = claim + 执行 + finish/fail）。"""
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task run')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--session-id', default=None, dest='session_id',
                            help='执行会话标识（默认: 环境变量 PLAN_TASK_SESSION_ID，不传则不落库）')
        parser.add_argument('--agent-name', default=None, dest='agent_name',
                            help='agent 外壳标识（默认: 环境变量 PLAN_TASK_AGENT_NAME，不传则不落库）')
        parser.add_argument('--format', dest='format', choices=['json', 'jsonl'], default='jsonl',
                            help='逐步骤结果输出格式（默认: jsonl 单行）')
        ns = parser.parse_args(args)

        session_id, agent_name = self._resolve_session(ns)
        rows = self._build_mgr().run_task(ns.task_id, session_id=session_id,
                                          agent_name=agent_name)
        if not rows:
            log.info("任务 %s 无可认领步骤（无 pending / 依赖均未就绪 / 非 pending-running 状态）；"
                     "用 status %s 看终态", ns.task_id, ns.task_id)
            return True
        for row in rows:
            log.info("步骤 %s「%s」%s（%s）", row.get('seq'), row.get('name'),
                     row.get('status'), row.get('summary'))
        self._emit(ctx, rows, ns.format)
        released = [r for r in rows if r.get('status') == 'released']
        if released:
            log.warning("存在非 bash 步骤（%s），已 release 还队列并停止；"
                        "该步骤需 AI 编排层接手，处理后再 run 续跑",
                        '、'.join(str(r.get('name')) for r in released))
        elif any(r.get('status') in ('failed', 'retried') for r in rows):
            log.warning("存在失败步骤（预算内已回 pending，本轮不自动重跑）；"
                        "排查后再次 run 续跑，或 retry <step_id> 重置")
        return True

    def _finish(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task finish')
        parser.add_argument('run_id', type=int, help='执行 id（claim 输出的 run_id）')
        output_group = parser.add_mutually_exclusive_group(required=False)
        output_group.add_argument('--output', default=None, help='执行输出原文')
        output_group.add_argument('--output-file', dest='output_file', default=None,
                                  help='从 UTF-8 文件读取执行输出；"-" 读 stdin')
        summary_group = parser.add_mutually_exclusive_group(required=False)
        summary_group.add_argument('--summary', default=None,
                                   help='一句话结果摘要（强烈建议必带：它是后续步骤的上下文来源）')
        summary_group.add_argument('--summary-file', dest='summary_file', default=None,
                                   help='从 UTF-8 文件读取结果摘要；"-" 读 stdin'
                                        '（长中文摘要绕开 shell argv 引号/编码问题）')
        parser.add_argument('--format', dest='format', choices=['json', 'jsonl'], default='jsonl',
                            help='成功回显步骤状态的输出格式（默认: jsonl）')
        parser.add_argument('--token-usage', dest='token_usage', type=int, default=None,
                            help='本次执行 token 消耗（可选回填，仅记录到 run 行）')
        ns = parser.parse_args(args)

        output = ns.output if ns.output is not None else (_read_text_source(ns.output_file)
                                                          or '')
        summary = ns.summary if ns.summary is not None else _read_text_source(ns.summary_file)
        if summary is None:
            log.warning("未提供 --summary/--summary-file，将截取 output 前 2000 字作为摘要；"
                        "后续步骤的续跑上下文质量会下降，建议必带")
        mgr = self._build_mgr()
        ok = mgr.finish_run(ns.run_id, output=output, summary=summary,
                            token_usage=ns.token_usage)
        if not ok:
            log.warning("run %s 收口失败（不存在或已终态，不重复流转）", ns.run_id)
            return False
        log.info("run %s 已成功收口", ns.run_id)
        run = mgr.get_run(ns.run_id)
        if run is not None and run.task_id is not None:
            step_row = next((s for s in mgr.list_steps(run.task_id)
                             if s.get('id') == run.step_id), None)
            if step_row is not None:
                ack = {'run_id': ns.run_id, 'step_id': run.step_id, 'task_id': run.task_id}
                ack.update({k: step_row.get(k) for k in
                            ('seq', 'name', 'status', 'result_summary', 'finished_at')})
                self._emit_one(ctx, self._apply_snippet([ack], 300,
                                                        fields=('result_summary',))[0], ns.format)
        return True

    def _fail(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task fail')
        parser.add_argument('run_id', type=int, help='执行 id（claim 输出的 run_id）')
        error_group = parser.add_mutually_exclusive_group(required=True)
        error_group.add_argument('--error', default=None, help='失败原因')
        error_group.add_argument('--error-file', dest='error_file', default=None,
                                 help='从 UTF-8 文件读取失败原因；"-" 读 stdin')
        ns = parser.parse_args(args)

        error = ns.error if ns.error is not None else _read_text_source(ns.error_file)
        assert error is not None
        disposition = self._build_mgr().fail_run(ns.run_id, error)
        if disposition == 'retried':
            log.info("run %s 失败已上报：预算未耗尽，步骤回 pending 待重试"
                     "（下次 claim 重新认领该步骤）", ns.run_id)
        elif disposition == 'step_failed':
            log.info("run %s 失败已上报：预算耗尽，步骤已终败、任务已置 failed"
                     "（可 retry <step_id> 手动再给一次机会）", ns.run_id)
        else:
            log.warning("run %s 未流转（不存在或已终态）", ns.run_id)
            return False
        return True

    def _heartbeat(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task heartbeat')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        ns = parser.parse_args(args)
        self._build_mgr().heartbeat(ns.task_id)
        log.info("task %s 心跳已刷新", ns.task_id)
        return True
    # endregion

    # region ======== 子命令实现：查询 ========
    def _status(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task status')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--snippet', type=int, default=300,
                            help='长字段预览字数（默认 300）；--full 时忽略')
        parser.add_argument('--full', action='store_true', help='不截断，输出全文')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args)

        mgr = self._build_mgr()
        task = mgr.get_task(ns.task_id)
        if task is None:
            log.error("任务不存在: id=%s（用 list 查看现有任务）", ns.task_id)
            return False
        steps = mgr.list_steps(ns.task_id)
        artifacts = mgr.list_artifacts(ns.task_id)
        counts = {s['status']: 0 for s in steps}
        for s in steps:
            counts[s['status']] = counts.get(s['status'], 0) + 1
        overview = task.to_dict()
        overview.update({
            'total': len(steps),
            'done': counts.get('succeeded', 0),
            'pending': counts.get('pending', 0),
            'running': counts.get('running', 0),
            'failed': counts.get('failed', 0),
            'skipped': counts.get('skipped', 0),
            'artifact_count': len(artifacts),
        })
        if not ns.full:
            self._apply_snippet([overview], ns.snippet)
        self._emit(ctx, [overview], ns.format)
        if steps:
            rows = steps if ns.full else self._apply_snippet(
                [dict(s) for s in steps], ns.snippet, fields=('instruction',))
            self._emit(ctx, rows, ns.format)
        return True

    def _list(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task list')
        parser.add_argument('--status', default=None,
                            help='限定任务状态（pending/running/paused/completed/failed/cancelled）')
        parser.add_argument('--created-by', default=None, dest='created_by',
                            help='限定创建者标签')
        parser.add_argument('--limit', type=int, default=50, help='最多返回条数（默认 50）')
        parser.add_argument('--snippet', type=int, default=300,
                            help='goal 预览字数（默认 300）；--full 时忽略')
        parser.add_argument('--full', action='store_true', help='不截断，goal 返回全文')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args)

        rows = self._build_mgr().list_tasks(status=ns.status, created_by=ns.created_by,
                                            limit=ns.limit)
        if not ns.full:
            self._apply_snippet(rows, ns.snippet)
        log.info("共 %d 个任务（status=%s, created_by=%s）", len(rows), ns.status,
                 ns.created_by)
        self._emit(ctx, rows, ns.format)
        return True
    # endregion

    # region ======== 子命令实现：生命周期 / 恢复 ========
    def _pause(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task pause')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        ns = parser.parse_args(args)
        ok = self._build_mgr().pause(ns.task_id)
        log.info("pause %s -> %s", ns.task_id, '已暂停（running 步骤不强杀，不再派发新步骤）'
                 if ok else '未命中（任务非 running）')
        return ok

    def _resume(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task resume')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        ns = parser.parse_args(args)
        ok = self._build_mgr().resume(ns.task_id)
        log.info("resume %s -> %s", ns.task_id, '已恢复 running' if ok else '未命中（任务非 paused）')
        return ok

    def _cancel(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task cancel')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--reason', default='', help='取消原因（记入事件）')
        ns = parser.parse_args(args)
        ok = self._build_mgr().cancel(ns.task_id, reason=ns.reason)
        log.info("cancel %s -> %s", ns.task_id, '已取消（running 步骤连带 failed）'
                 if ok else '未命中（任务已终态或不存在）')
        return ok

    def _retry(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task retry')
        parser.add_argument('step_id', type=int, help='目标步骤 id')
        parser.add_argument('--force', action='store_true',
                            help='管理强制：额外放行 succeeded（清假摘要，completed 任务一并复活）')
        ns = parser.parse_args(args)
        ok = self._build_mgr().retry_step(ns.step_id, force=ns.force)
        log.info("retry %s -> %s", ns.step_id,
                 '已回 pending（预算+1；任务若 failed/completed 已复活 running）'
                 if ok else '未命中（步骤非 failed/skipped；succeeded 需 --force）')
        return ok

    def _skip(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task skip')
        parser.add_argument('step_id', type=int, help='目标步骤 id')
        parser.add_argument('--reason', default='', help='跳过原因（记入事件）')
        ns = parser.parse_args(args)
        ok = self._build_mgr().skip_step(ns.step_id, reason=ns.reason)
        log.info("skip %s -> %s", ns.step_id, '已跳过' if ok else '未命中（步骤非 pending）')
        return ok

    def _sweep(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task sweep')
        parser.add_argument('--heartbeat-timeout-sec', type=int, default=None,
                            dest='heartbeat_timeout_sec',
                            help='心跳超时阈值秒的全局覆盖（默认逐任务用其自身配置）')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args)

        results = self._build_mgr().sweep(heartbeat_timeout_sec=ns.heartbeat_timeout_sec)
        if not results:
            log.info("无僵尸对象（心跳与单步超时均正常）")
            return True
        log.info("sweep 恢复了 %d 个对象（被恢复步骤等下次 claim 续跑）", len(results))
        self._emit(ctx, results, ns.format)
        return True

    def _release(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task release')
        parser.add_argument('run_id', type=int, help='执行 id（claim 输出的 run_id）')
        parser.add_argument('--reason', default='', help='释放原因（记入事件），如会话结束/上游未就绪')
        ns = parser.parse_args(args)
        result = self._build_mgr().release_run(ns.run_id, reason=ns.reason)
        if result != 'released':
            log.warning("run %s 未流转（不存在或已终态）", ns.run_id)
            return False
        log.info("run %s 已释放：步骤回 pending（重试预算不变），等下次 claim 续跑", ns.run_id)
        return True

    def _verify(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task verify')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--fix', action='store_true',
                            help='就地修复（全部修复同一事务执行，并追加 warn 级汇总事件留痕）')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args)

        findings = self._build_mgr().verify_task(ns.task_id, fix=ns.fix)
        n_fixed = sum(1 for f in findings if f.get('fixed'))
        if not findings:
            log.info("verify task=%s：事件流水与状态一致，未发现异常", ns.task_id)
            return True
        log.info("verify task=%s：发现 %d 处异常%s", ns.task_id, len(findings),
                 f"，已修复 {n_fixed} 处" if ns.fix else "（加 --fix 就地修复）")
        self._emit(ctx, findings, ns.format)
        return True
    # endregion

    # region ======== 子命令实现：产物 / 事件 ========
    def _artifact(self, ctx: CliContext, args: list[str]) -> bool:
        if not args:
            log.error("artifact 需要二级子命令: add | list")
            return False
        sub, rest = args[0], args[1:]
        if sub == 'add':
            return self._artifact_add(ctx, rest)
        if sub == 'list':
            return self._artifact_list(ctx, rest)
        log.error(f"未知二级子命令: artifact {sub}")
        return False

    def _artifact_add(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task artifact add')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--path', required=True, help='产物路径（相对仓库根或绝对路径）')
        parser.add_argument('--type', default='file', dest='art_type',
                            choices=sorted(VALID_ART_TYPES),
                            help='产物类型（默认 file）')
        parser.add_argument('--step', type=int, default=None, help='所属步骤 id（默认任务级）')
        parser.add_argument('--note', default=None, help='备注')
        ns = parser.parse_args(args)
        art_id = self._build_mgr().add_artifact(ns.task_id, ns.art_type, ns.path,
                                                step_id=ns.step, note=ns.note)
        log.info("产物已登记 id=%s (task=%s, type=%s, path=%s)", art_id, ns.task_id,
                 ns.art_type, ns.path)
        return True

    def _artifact_list(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task artifact list')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args)
        rows = self._build_mgr().list_artifacts(ns.task_id)
        self._emit(ctx, rows, ns.format)
        return True

    def _event(self, ctx: CliContext, args: list[str]) -> bool:
        if not args or args[0] != 'list':
            log.error("event 需要二级子命令: list")
            return False
        parser = argparse.ArgumentParser(prog='python -m baibao plan_task event list')
        parser.add_argument('task_id', type=int, help='目标任务 id')
        parser.add_argument('--limit', type=int, default=100,
                            help='最近事件条数（默认 100）')
        parser.add_argument('--format', dest='format',
                            choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                            help='输出格式（默认: jsonl）')
        ns = parser.parse_args(args[1:])
        rows = self._build_mgr().list_events(ns.task_id, limit=ns.limit)
        self._emit(ctx, rows, ns.format)
        return True
    # endregion

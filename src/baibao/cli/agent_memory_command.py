"""
agent_memory 命令 - AI 记忆的高层操作（remember / recall / update / forget 等）。

基于 :mod:`baibao.ai_agent.memory` 的 ``RdbMemoryStore``，对外封装为面向 AI/用户
的高层子命令；调用方只需提供业务字段，无需编写 SQL。

子命令概览：
  - init       幂等建表 + 自检计数
  - remember   记一条事实（INSERT，含 scope+title 去重提示）
  - recall     模糊检索（多关键词多字段加权，命中自动累加计数）
  - update     按 id 部分更新
  - forget     按 id 软删除
  - get        按 id 取单条
  - list       浏览（按 scope/category 过滤，不计命中次数）
  - count      统计条数

身份与角色：
  - ``--owner`` / ``--owner-group``：当前身份（用户/团队）；缺省时按
    环境变量 ``AGENT_MEMORY_OWNER`` / ``AGENT_MEMORY_OWNER_GROUP`` →
    配置文件（``.baibao/agent_memory.config``）解析。
  - ``--machine`` / ``--agent-name``：物理机/agent 外壳标签（仅 remember 盖章 + machine 去重用）。
    machine 注入优先级：``--machine`` > ``AGENT_MEMORY_MACHINE`` > 配置 > ``socket.gethostname()``；
    agent_name 无自动探测源，未配置则为空。
  - ``--dedup {auto,machine,global}``（仅 remember）：去重维度。auto（默认）按 category 自动
    （路径类按 machine，其余按 global）；machine/global 显式覆盖。
  - ``--shared``：切换为**共享角色**——owner 被忽略，仅查看/操作 owner 为空的共享数据，
    个人数据不可见不可改。改/删共享数据必须加此开关。

  鉴权模型详见 :mod:`pykunlun.ai_agent.memory` 的 ``visibility_clause`` / ``permission_clause``。
  machine/agent_name 为纯标签字段，**不参与鉴权与过滤**，recall 时仅随结果返回供 AI 自判。
"""

import argparse
import csv
import io
import json
import os
import socket
import sys
from datetime import date, datetime
from typing import Any

from pykunlun.ai_agent import (
    PATH_LIKE_CATEGORIES,
    VALID_CATEGORIES,
    MemoryManager,
    MemoryRecord,
)
from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.ai_agent.memory import RdbMemoryStore

log = logutil.getLogger(__name__)

#: 身份配置文件名
_CONFIG_FILENAME = 'agent_memory.config'

#: 配置缓存（一次进程内复用）
_config_cache: dict[str, Any] | None = None


def _detect_machine() -> str | None:
    """自动探测当前物理机名（socket.gethostname），失败时返回 None。"""
    try:
        name = socket.gethostname()
        return name.strip() or None
    except Exception:
        log.warning("探测 hostname 失败，machine 字段将以 None 记入", exc_info=True)
        return None


def _load_config() -> dict[str, Any]:
    """搜索并加载身份配置文件，返回字典（无则为空 dict）。优先级：先找到的为准。

    搜索范围仅 ``.baibao/``（当前目录优先，再用户目录），与 rdb.config 一致——
    不读技能目录，避免多 agent 安装点之间配置歧义。
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    paths = [
        os.path.join(os.getcwd(), '.baibao', _CONFIG_FILENAME),
        os.path.join(os.path.expanduser('~'), '.baibao', _CONFIG_FILENAME),
    ]
    for p in paths:
        try:
            if os.path.isfile(p):
                # utf-8-sig 容忍编辑器写入的 BOM
                with open(p, encoding='utf-8-sig') as f:
                    data: dict[str, Any] = json.load(f)
                if isinstance(data, dict):
                    log.debug("加载记忆配置: %s", p)
                    _config_cache = data
                    return data
        except Exception:
            log.warning("读取记忆配置失败: %s", p, exc_info=True)
    _config_cache = {}
    return _config_cache


def _read_text_source(path: str | None) -> str | None:
    """从文件或 stdin 读取长文本，绕开命令行 shell 引号转义问题。

    供 ``--content-file`` 等参数使用：内容里的引号/特殊字符在 Windows PowerShell
    等 shell 下经 argv 传递时易被剥离（CRT 在 Python 之前解析命令行），改走文件/stdin 可靠。

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


class _CustomEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，处理日期时间类型。"""

    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


class AgentMemoryCommand(Command):
    """
    AI 记忆命令。

    每次调用按配置（环境变量/配置文件）解析 rdb 实例名，构造绑定 ``--owner`` 身份的
    :class:`RdbMemoryStore`，并经 :class:`MemoryManager` 转发（recall 自动叠加命中计数）。
    """

    @property
    def name(self) -> str:
        return 'agent_memory'

    @property
    def abbr(self) -> str:
        return 'am'

    @property
    def description(self) -> str:
        return 'AI 记忆操作（记/回忆/更新/遗忘事实类项目知识）'

    @property
    def usage(self) -> str:
        return (
            "python -m baibao agent_memory <子命令> [选项]\n"
            "\n"
            "子命令:\n"
            "  init                                         幂等建表 + 自检计数\n"
            "  remember  --scope --category --title --content/--content-file  记一条事实\n"
            "  recall    <关键词...> [--scope --category --limit]  模糊检索\n"
            "  update    <id> [--title --content/--content-file ...]  按 id 部分更新\n"
            "  forget    <id>                               按 id 软删除\n"
            "  get       <id>                               按 id 取单条\n"
            "  list      [--scope --category --limit]       浏览（不计命中次数）\n"
            "  count                                        统计条数\n"
            "\n"
            "身份与角色:\n"
            "  --owner NAME      当前用户标识（默认按 环境变量>配置文件 解析）\n"
            "  --owner-group G   当前团队/组（标签，仅 remember 盖章用）\n"
            "  --machine NAME    当前物理机标识（标签，默认: 配置>环境变量>socket.gethostname()）\n"
            "  --agent-name NAME 当前 agent 外壳标识（标签，默认: 配置>环境变量；无自动探测）\n"
            "  --shared          切换为共享角色：仅查看/操作共享数据，忽略 owner，个人不可见\n"
            "\n"
            "去重维度（仅 remember）:\n"
            "  --dedup {auto,machine,global}  auto=按 category 自动（默认），machine=按 scope+title+machine，global=按 scope+title\n"
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
            if sub == 'remember':
                return self._remember(ctx, rest)
            if sub == 'recall':
                return self._recall(ctx, rest)
            if sub == 'update':
                return self._update(ctx, rest)
            if sub == 'forget':
                return self._forget(ctx, rest)
            if sub == 'get':
                return self._get(ctx, rest)
            if sub == 'list':
                return self._list(ctx, rest)
            if sub == 'count':
                return self._count(ctx, rest)
            if sub in ('-h', '--help'):
                self.show_usage()
                return True
            log.error(f"未知子命令: {sub}")
            self.show_usage()
            return False
        except Exception as e:
            log.error(f"agent_memory {sub} 失败: {e}")
            return False

    # region ======== 工具：身份解析与构造 ========

    @staticmethod
    def _resolve(ns: argparse.Namespace) -> tuple[str | None, str | None, str | None, str | None, bool]:
        """解析 (owner, owner_group, machine, agent_name, shared_mode)。

        优先级（machine / agent_name 同 owner 套路：标志 > 环境变量 > 配置文件），
        其中 machine 末尾兜底 ``socket.gethostname()``（自动探测），agent_name 无探测源。
        """
        cfg = _load_config()
        owner = (getattr(ns, 'owner', None)
                 or os.environ.get('AGENT_MEMORY_OWNER')
                 or cfg.get('owner'))
        owner_group = (getattr(ns, 'owner_group', None)
                       or os.environ.get('AGENT_MEMORY_OWNER_GROUP')
                       or cfg.get('owner_group'))
        machine = (getattr(ns, 'machine', None)
                   or os.environ.get('AGENT_MEMORY_MACHINE')
                   or cfg.get('machine')
                   or _detect_machine())
        agent_name = (getattr(ns, 'agent_name', None)
                      or os.environ.get('AGENT_MEMORY_AGENT_NAME')
                      or cfg.get('agent_name'))
        shared = bool(getattr(ns, 'shared', False))
        if owner is None and not shared:
            log.info("未配置 owner，以无身份运行（仅共享域可见可写）；"
                     "多用户共享库建议在配置文件设置 owner")
        return owner, owner_group, machine, agent_name, shared

    @staticmethod
    def _build_mgr(owner: str | None, owner_group: str | None,
                   machine: str | None = None, agent_name: str | None = None) -> MemoryManager:
        """构造绑定身份的临时 MemoryManager（rdb 实例名走配置，调用方无需关心）。"""
        cfg = _load_config()
        db_name = os.environ.get('AGENT_MEMORY_DB') or cfg.get('rdb_name')
        mgr = MemoryManager()
        mgr.register(MemoryManager.DEFAULT_NAME,
                     RdbMemoryStore(db_name=db_name, owner=owner, owner_group=owner_group,
                                    machine=machine, agent_name=agent_name))
        return mgr

    @staticmethod
    def _common(parser: argparse.ArgumentParser, output: bool = False) -> None:
        """添加通用参数：--owner/--owner-group/--machine/--agent-name/--shared（+ output 时含 --format）。"""
        parser.add_argument('--owner', default=None, help='当前用户标识（默认: 配置>环境变量）')
        parser.add_argument('--owner-group', default=None, help='当前团队/组（标签）')
        parser.add_argument('--machine', default=None,
                            help='当前物理机标识（标签，默认: 配置>环境变量>socket.gethostname()）')
        parser.add_argument('--agent-name', default=None, dest='agent_name',
                            help='当前 agent 外壳标识（标签，默认: 配置>环境变量；无自动探测）')
        parser.add_argument('--shared', action='store_true',
                            help='切换为共享角色：仅查看/操作共享数据，忽略 owner')
        if output:
            parser.add_argument('--format', dest='format',
                                choices=['json', 'jsonl', 'csv', 'table'], default='jsonl',
                                help='输出格式（默认: jsonl）')

    def _emit(self, ctx: CliContext, rows: list[dict[str, Any]], fmt: str) -> None:
        ctx.print_delim()
        self._format_result(rows, fmt)
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
    def _apply_snippet(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """对 rows 的 content 做预览截断（recall/list 用，节省 AI 上下文）。

        折叠空白（含换行）为单行预览；超 limit 则截断并追加内联指示
        ``…（+N字，get <id> 看全文）``。短内容也折叠换行；要原样换行走 get 或 --full。
        """
        for r in rows:
            content = r.get('content')
            if not isinstance(content, str):
                continue
            full_len = len(content)
            preview = ' '.join(content[:limit].split())
            if full_len > limit:
                preview = f'{preview}…（+{full_len - limit} 字，get {r.get("id")} 看全文）'
            r['content'] = preview
        return rows

    # endregion

    # region ======== 子命令实现 ========

    def _init(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory init')
        self._common(parser)
        ns = parser.parse_args(args)
        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        mgr.init_store()
        n = mgr.count(shared_mode=shared)
        role = '共享角色' if shared else (owner or '无身份')
        ctx.print_delim()
        print(f"记忆库已就绪（role={role}），当前 {n} 条可见记忆")
        ctx.print_delim()
        return True

    def _remember(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory remember')
        parser.add_argument('--scope', required=True, help='作用域（项目名/模块名）')
        parser.add_argument('--category', required=True, choices=sorted(VALID_CATEGORIES),
                            help='事实类型')
        parser.add_argument('--title', required=True, help='一句话摘要')
        content_group = parser.add_mutually_exclusive_group(required=True)
        content_group.add_argument('--content', default=None, help='完整内容（与 --content-file 二选一）')
        content_group.add_argument('--content-file', dest='content_file', default=None,
                                   help='从 UTF-8 文件读取完整内容；"-" 读 stdin'
                                        '（适合含引号/特殊字符/超长内容，绕开 shell argv 引号剥离）')
        parser.add_argument('--keywords', default='', help='逗号分隔的关键词/标签')
        parser.add_argument('--source', default='user-told',
                            help='来源（user-told/code-derived/inferred，默认 user-told）')
        parser.add_argument('--confidence', type=int, default=80, help='置信度 0~100（默认 80）')
        parser.add_argument('--pinned', type=int, choices=[0, 1], default=0, help='是否置顶（默认 0）')
        parser.add_argument('--force', action='store_true', help='同 scope+title 已存在时仍追加')
        parser.add_argument('--dedup', choices=['auto', 'machine', 'global'], default='auto',
                            help='去重维度：auto（默认，按 category 自动）/ machine（按 scope+title+machine，'
                                 '路径类语义）/ global（按 scope+title 全局，通用知识语义）。'
                                 'auto 时 file-path 等路径类自动按 machine，其余按 global')
        self._common(parser)
        ns = parser.parse_args(args)

        # content：互斥必填组保证恰好一个；--content-file 时走文件/stdin
        content = ns.content if ns.content is not None else _read_text_source(ns.content_file)
        assert content is not None

        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        # 去重维度翻译：--dedup 显式优先；auto 时按 category 是否路径类判定。
        # machine_bound 要求 machine 已绑定；显式 machine+无 machine 是违反意图，报错拒绝；
        # auto+路径类+无 machine 自动降级 global 并提示。
        if ns.dedup == 'machine':
            if not machine:
                log.error("--dedup machine 要求本机隔离判重，但未解析到 machine；"
                          "请用 --machine 指定、设环境变量 AGENT_MEMORY_MACHINE、"
                          "在配置文件配置 machine，或改用 --dedup global")
                return False
            machine_bound = True
        elif ns.dedup == 'global':
            machine_bound = False
        else:  # auto
            if ns.category in PATH_LIKE_CATEGORIES and not machine:
                log.info("路径类 category=%s 但未配置 machine，按全局判重；"
                         "建议配置 machine 以启用本机隔离", ns.category)
            machine_bound = (ns.category in PATH_LIKE_CATEGORIES) and bool(machine)
        # 去重：在同角色可见范围内按 scope+title 查（machine_bound 时叠加本机隔离）
        dups = mgr.find_by_scope_title(ns.scope, ns.title, shared_mode=shared,
                                       machine_bound=machine_bound)
        if dups and not ns.force:
            log.warning("发现同 scope+title 的已有记忆 %d 条（id=%s，dedup=%s，machine_bound=%s）；"
                        "如需追加新条目请加 --force，或改用 update 修改",
                        len(dups), ', '.join(str(d.id) for d in dups), ns.dedup, machine_bound)
            return False
        record = MemoryRecord(
            scope=ns.scope, category=ns.category, title=ns.title, content=content,
            keywords=ns.keywords, source=ns.source, confidence=ns.confidence, pinned=ns.pinned,
        )
        rid = mgr.remember(record, shared_mode=shared)
        kind = '共享记忆' if shared else '个人记忆'
        log.info("已记入%s id=%s (scope=%s, category=%s, owner=%s, group=%s, machine=%s, agent=%s)",
                 kind, rid, ns.scope, ns.category, owner, owner_group, machine, agent_name)
        return True

    def _recall(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory recall')
        parser.add_argument('query', nargs='+', help='查询关键词（空格分隔）')
        parser.add_argument('--scope', default=None, help='限定作用域（默认不限）')
        parser.add_argument('--category', default=None, help='限定事实类型（默认不限）')
        parser.add_argument('--limit', type=int, default=20, help='最多返回条数（默认 20）')
        parser.add_argument('--snippet', type=int, default=300,
                            help='content 预览字数（默认 300，折叠换行+截断+内联指示）；--full 时忽略')
        parser.add_argument('--full', action='store_true', help='不截断，content 返回全文+原样换行')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        rows = mgr.recall(' '.join(ns.query), scope=ns.scope, category=ns.category,
                          limit=ns.limit, shared_mode=shared)
        if not ns.full:
            self._apply_snippet(rows, ns.snippet)
        self._emit(ctx, rows, ns.format)
        return True

    def _update(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory update')
        parser.add_argument('id', type=int, help='待更新的记忆 id')
        parser.add_argument('--scope', default=None)
        parser.add_argument('--category', default=None, choices=sorted(VALID_CATEGORIES))
        parser.add_argument('--title', default=None)
        content_group = parser.add_mutually_exclusive_group(required=False)
        content_group.add_argument('--content', default=None)
        content_group.add_argument('--content-file', dest='content_file', default=None,
                                   help='从 UTF-8 文件读取新内容；"-" 读 stdin（与 --content 互斥）')
        parser.add_argument('--keywords', default=None)
        parser.add_argument('--source', default=None)
        parser.add_argument('--confidence', type=int, default=None)
        parser.add_argument('--pinned', type=int, choices=[0, 1], default=None)
        self._common(parser)
        ns = parser.parse_args(args)

        fields: dict[str, Any] = {}
        # content：--content 与 --content-file 互斥（都不给则不更新）；其余字段按是否提供收集
        if ns.content is not None:
            fields['content'] = ns.content
        elif ns.content_file is not None:
            fields['content'] = _read_text_source(ns.content_file)
        for k in ('scope', 'category', 'title', 'keywords', 'source',
                  'confidence', 'pinned'):
            v = getattr(ns, k)
            if v is not None:
                fields[k] = v
        if not fields:
            log.error("未指定任何待更新字段")
            return False
        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        ok = mgr.update(ns.id, fields, shared_mode=shared)
        log.info("update id=%s -> %s", ns.id, '命中' if ok else '未命中（不存在/已删除/无权限）')
        return ok

    def _forget(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory forget')
        parser.add_argument('id', type=int, help='待软删除的记忆 id')
        self._common(parser)
        ns = parser.parse_args(args)
        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        ok = mgr.forget(ns.id, shared_mode=shared)
        log.info("forget id=%s -> %s", ns.id, '已软删除' if ok else '未命中（不存在/已删除/无权限）')
        return ok

    def _get(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory get')
        parser.add_argument('id', type=int, help='记忆 id')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        rec = mgr.get(ns.id, shared_mode=shared)
        if rec is None:
            log.info("未找到 id=%s 的记忆（可能不可见）", ns.id)
            return True
        self._emit(ctx, [rec.to_dict()], ns.format)
        return True

    def _list(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory list')
        parser.add_argument('--scope', default=None, help='限定作用域')
        parser.add_argument('--category', default=None, help='限定事实类型')
        parser.add_argument('--limit', type=int, default=20, help='最多返回条数（默认 20）')
        parser.add_argument('--snippet', type=int, default=300,
                            help='content 预览字数（默认 300，折叠换行+截断+内联指示）；--full 时忽略')
        parser.add_argument('--full', action='store_true', help='不截断，content 返回全文+原样换行')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        # 浏览：空查询召回可见范围（按 pinned/最近使用排序），不计命中次数（touch=False）
        rows = mgr.recall('', scope=ns.scope, category=ns.category, limit=ns.limit,
                          touch=False, shared_mode=shared)
        if not ns.full:
            self._apply_snippet(rows, ns.snippet)
        self._emit(ctx, rows, ns.format)
        return True

    def _count(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_memory count')
        parser.add_argument('--all', action='store_true', help='包含软删除项')
        self._common(parser)
        ns = parser.parse_args(args)
        owner, owner_group, machine, agent_name, shared = self._resolve(ns)
        mgr = self._build_mgr(owner, owner_group, machine, agent_name)
        n = mgr.count(include_deleted=ns.all, shared_mode=shared)
        ctx.print_delim()
        print(f"{n} 条记忆{'（含软删除）' if ns.all else ''}")
        ctx.print_delim()
        return True

    # endregion

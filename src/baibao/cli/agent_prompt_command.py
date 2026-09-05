"""
agent_prompt 命令 - AI prompt 模板库的高层操作（save / search / get / render 等）。

基于 :mod:`baibao.ai_agent.prompt` 的 ``RdbPromptStore``，对外封装为面向 AI/用户
的高层子命令；调用方只需提供模板字段，无需编写 SQL。

子命令概览：
  - init     幂等建表 + 自检计数
  - save     存一条模板（INSERT，同名查重拒绝；--force 覆盖更新）
  - list     浏览（按取用热度排序，不计取用次数）
  - search   模糊检索（多关键词多字段加权）
  - get      按 name 取模板"即用包"（元信息+变量清单+块清单+正文全文），自动累加取用计数
  - render   确定性渲染：填变量/裁剪可选块/剥离标记（--clip 复制到剪贴板，外送其他 AI 工具）
  - update   按 name 部分更新（白名单字段）
  - forget   按 name 软删除
  - export / import  markdown 交换格式（frontmatter + 正文）
  - tags     标签聚合
  - stats    取用排行

身份与角色（与 agent_memory 同模型）：
  - ``--owner``：当前身份；缺省按 环境变量 ``AGENT_PROMPT_OWNER`` →
    配置文件（``.baibao/agent_prompt.config``）解析。
  - ``--shared``：共享库显式开关——save 共享模板、update/forget 共享模板必须加；
    个人模板不加（无身份时 save 个人模板直接报错，防默认值陷阱）。

rdb 实例名优先级：环境变量 ``AGENT_PROMPT_DB`` → 配置 ``rdb_name`` → 缺省 ``ai_agent``。
"""

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from datetime import date, datetime
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.ai_agent.prompt import (
    RdbPromptStore,
    markdown_to_template,
    parse_variables,
    render_template,
    template_to_markdown,
)

log = logutil.getLogger(__name__)

#: 身份配置文件名
_CONFIG_FILENAME = 'agent_prompt.config'

#: rdb 实例名缺省值（at/am 同库，团队零新增部署）
_DEFAULT_DB_NAME = 'ai_agent'

#: 配置缓存（一次进程内复用）
_config_cache: dict[str, Any] | None = None


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
                    log.debug("加载 prompt 库配置: %s", p)
                    _config_cache = data
                    return data
        except Exception:
            log.warning("读取 prompt 库配置失败: %s", p, exc_info=True)
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


class _CustomEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，处理日期时间类型。"""

    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


def _copy_windows_clipboard(text: str) -> bool:
    """ctypes 直调 Win32 剪贴板 API（CF_UNICODETEXT）。

    优先于 ``clip.exe``：后者在无控制台的会话（IDE/服务/管道）里会"拒绝访问"。
    """
    import ctypes
    from ctypes import wintypes

    cf_unicodetext, gmem_moveable = 13, 0x0002
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode('utf-16-le') + b'\x00\x00'
        handle = kernel32.GlobalAlloc(gmem_moveable, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        # 成功后句柄归系统所有，不可 GlobalFree
        return bool(user32.SetClipboardData(cf_unicodetext, handle))
    finally:
        user32.CloseClipboard()


def _copy_to_clipboard(text: str) -> bool:
    """把文本写入系统剪贴板；失败仅告警不中断（正文仍已打印到 stdout）。

    Windows 优先 ctypes 直调剪贴板 API，``clip.exe`` 兜底；macOS ``pbcopy``；
    Linux ``xclip``。
    """
    try:
        if sys.platform == 'win32':
            if _copy_windows_clipboard(text):
                return True
            subprocess.run(['clip'], input=text.encode('utf-16'), check=True)
        elif sys.platform == 'darwin':
            subprocess.run(['pbcopy'], input=text.encode('utf-8'), check=True)
        else:
            subprocess.run(['xclip', '-selection', 'clipboard'],
                           input=text.encode('utf-8'), check=True)
        return True
    except Exception:
        log.warning("写剪贴板失败（Windows: clipboard API/clip / macOS: pbcopy / Linux: xclip）",
                    exc_info=True)
        return False


def _parse_set_items(items: list[str] | None) -> dict[str, str] | None:
    """解析 --set 的 ``k=v`` 列表为 dict；存在非法项（无 =）时返回 None 并告警。"""
    if not items:
        return {}
    result: dict[str, str] = {}
    for it in items:
        k, sep, v = it.partition('=')
        if not sep or not k.strip():
            log.error("--set 格式应为 名=值，收到: %r", it)
            return None
        result[k.strip()] = v
    return result


def _parse_name_set(arg: str | None, flag: str) -> set[str] | None:
    """解析 --with/--without 的逗号分隔块名列表为 set；格式非法返回 None。"""
    if arg is None:
        return set()
    result = {x.strip() for x in arg.split(',') if x.strip()}
    if not result:
        log.error("%s 不能为空", flag)
        return None
    return result


class AgentPromptCommand(Command):
    """
    AI prompt 模板库命令。

    每次调用按配置（环境变量/配置文件）解析 rdb 实例名与身份，构造绑定
    ``--owner`` 身份的 :class:`RdbPromptStore`。
    """

    @property
    def name(self) -> str:
        return 'agent_prompt'

    @property
    def abbr(self) -> str:
        return 'ap'

    @property
    def description(self) -> str:
        return 'AI prompt 模板库（存/搜/取/渲染给 AI 的任务 prompt 模板）'

    @property
    def usage(self) -> str:
        return (
            "python -m baibao agent_prompt <子命令> [选项]\n"
            "\n"
            "子命令:\n"
            "  init                                      幂等建表 + 自检计数\n"
            "  save    --name --title --content/--content-file  存一条模板\n"
            "  list    [--tag --limit]                   浏览（不计取用次数）\n"
            "  search  <关键词...> [--tag --limit]        模糊检索\n"
            "  get     <name> [--no-count]               取模板即用包（元信息+变量+块+正文）\n"
            "  render  <name> [--set k=v] [--with 块] [--without 块] [--clip]  确定性渲染\n"
            "  update  <name> [--title --tags --content-file ...]  部分更新\n"
            "  forget  <name> [--shared]                 按 name 软删除\n"
            "  export  <name> --out F                    导出 markdown 交换格式\n"
            "  import  --file F [--shared --force]       从 markdown 导入\n"
            "  tags                                      标签聚合\n"
            "  stats  [--limit N]                        取用排行\n"
            "\n"
            "身份与角色:\n"
            "  --owner NAME   当前用户标识（默认按 环境变量>配置文件 解析）\n"
            "  --shared       共享库显式开关：save/update/forget 共享模板必须加；\n"
            "                 个人模板不加（无身份时 save 个人模板报错，防默认值陷阱）\n"
            "\n"
            "通用选项:\n"
            "  --format FMT   输出格式: json|jsonl|csv|table（默认: jsonl）\n"
            "  -h, --help     显示帮助信息"
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
            if sub == 'save':
                return self._save(ctx, rest)
            if sub == 'list':
                return self._list(ctx, rest)
            if sub == 'search':
                return self._search(ctx, rest)
            if sub == 'get':
                return self._get(ctx, rest)
            if sub == 'render':
                return self._render(ctx, rest)
            if sub == 'update':
                return self._update(ctx, rest)
            if sub == 'forget':
                return self._forget(ctx, rest)
            if sub == 'export':
                return self._export(ctx, rest)
            if sub == 'import':
                return self._import(ctx, rest)
            if sub == 'tags':
                return self._tags(ctx, rest)
            if sub == 'stats':
                return self._stats(ctx, rest)
            if sub in ('-h', '--help'):
                self.show_usage()
                return True
            log.error(f"未知子命令: {sub}")
            self.show_usage()
            return False
        except Exception as e:
            log.error(f"agent_prompt {sub} 失败: {e}")
            return False

    # region ======== 工具：身份解析与构造 ========

    @staticmethod
    def _resolve_owner(ns: argparse.Namespace) -> str | None:
        """解析 owner。优先级：标志 > 环境变量 > 配置文件。"""
        owner = (getattr(ns, 'owner', None)
                 or os.environ.get('AGENT_PROMPT_OWNER')
                 or _load_config().get('owner'))
        if owner is None:
            log.info("未配置 owner，以无身份运行（仅共享模板可见；个人模板需配置 owner）")
        return owner

    @staticmethod
    def _build_store(owner: str | None) -> RdbPromptStore:
        """构造绑定身份的存储（rdb 实例名走配置，缺省 ai_agent 与 at/am 同库）。"""
        db_name = (os.environ.get('AGENT_PROMPT_DB')
                   or _load_config().get('rdb_name')
                   or _DEFAULT_DB_NAME)
        return RdbPromptStore(db_name=db_name, owner=owner)

    @staticmethod
    def _common(parser: argparse.ArgumentParser, output: bool = False,
                shared: bool = False) -> None:
        """添加通用参数：--owner（+ shared 时含 --shared，+ output 时含 --format）。"""
        parser.add_argument('--owner', default=None,
                            help='当前用户标识（默认: 配置>环境变量）')
        if shared:
            parser.add_argument('--shared', action='store_true',
                                help='共享库显式开关：save/update/forget 共享模板必须加')
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
        """对 rows 的 content 做预览截断（list/search 用，节省 AI 上下文）。

        折叠空白（含换行）为单行预览；超 limit 则截断并追加内联指示
        ``…（+N字，get <name> 看全文）``。
        """
        for r in rows:
            content = r.get('content')
            if not isinstance(content, str):
                continue
            full_len = len(content)
            preview = ' '.join(content[:limit].split())
            if full_len > limit:
                preview = f'{preview}…（+{full_len - limit} 字，get {r.get("name")} 看全文）'
            r['content'] = preview
        return rows

    # endregion

    # region ======== 子命令实现 ========

    def _init(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt init')
        self._common(parser)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        store.init_store()
        n = store.count()
        ctx.print_delim()
        print(f"模板库已就绪（db={store.db_name}，role={store.owner or '无身份(仅共享)'}），"
              f"当前 {n} 条可见模板")
        ctx.print_delim()
        return True

    def _save(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt save')
        parser.add_argument('--name', required=True,
                            help='调用键（英文小写短名，全局唯一，如 code-review）')
        parser.add_argument('--title', required=True, help='中文标题')
        parser.add_argument('--description', default='', help='何时用这个模板')
        parser.add_argument('--tags', default='', help='逗号分隔的标签')
        content_group = parser.add_mutually_exclusive_group(required=True)
        content_group.add_argument('--content', default=None,
                                   help='模板正文（与 --content-file 二选一）')
        content_group.add_argument('--content-file', dest='content_file', default=None,
                                   help='从 UTF-8 文件读取正文；"-" 读 stdin'
                                        '（适合含引号/特殊字符/超长内容，绕开 shell argv 引号剥离）')
        parser.add_argument('--vars-file', dest='vars_file', default=None,
                            help='变量元信息 JSON 文件：[{name,desc,example,required,default}]；'
                                 '缺省按正文自动扫描 {{var}} 生成')
        parser.add_argument('--force', action='store_true',
                            help='同名已存在时覆盖更新（保留 id/归属/创建信息）')
        self._common(parser, shared=True)
        ns = parser.parse_args(args)

        content = ns.content if ns.content is not None else _read_text_source(ns.content_file)
        assert content is not None
        vars_meta: list[dict[str, Any]] | None = None
        if ns.vars_file is not None:
            raw = _read_text_source(ns.vars_file)
            try:
                parsed: list[Any] = json.loads(raw or '')
            except ValueError as e:
                log.error("--vars-file 不是合法 JSON: %s", e)
                return False
            if not isinstance(parsed, list):
                log.error("--vars-file 顶层不是 JSON 数组")
                return False
            vars_meta = parsed

        store = self._build_store(self._resolve_owner(ns))
        result = store.save(name=ns.name, title=ns.title, content=content,
                            description=ns.description, tags=ns.tags,
                            vars_meta=vars_meta, shared=ns.shared, force=ns.force)
        row = result['row']
        compact = {k: row.get(k) for k in
                   ('id', 'name', 'title', 'tags', 'owner', 'created_by', 'updated_at')}
        compact['action'] = result['action']
        compact['vars'] = row.get('vars')
        self._emit(ctx, [compact], 'jsonl')
        variables = parse_variables(content)
        log.info("已%s模板 id=%s name=%s owner=%s（%s）",
                 '覆盖更新' if result['action'] == 'updated' else '存入',
                 row['id'], ns.name, row.get('owner') or '共享',
                 f"变量: {', '.join(variables)}" if variables else '无变量')
        return True

    def _list(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt list')
        parser.add_argument('--tag', default=None, help='按标签过滤（子串匹配）')
        parser.add_argument('--limit', type=int, default=50, help='最多返回条数（默认 50）')
        parser.add_argument('--snippet', type=int, default=300,
                            help='content 预览字数（默认 300，折叠换行+截断+内联指示）；--full 时忽略')
        parser.add_argument('--full', action='store_true', help='不截断，content 返回全文+原样换行')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        rows = store.list_templates(tag=ns.tag, limit=ns.limit)
        if not ns.full:
            self._apply_snippet(rows, ns.snippet)
        self._emit(ctx, rows, ns.format)
        return True

    def _search(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt search')
        parser.add_argument('query', nargs='+', help='查询关键词（空格分隔）')
        parser.add_argument('--tag', default=None, help='按标签过滤（子串匹配）')
        parser.add_argument('--limit', type=int, default=20, help='最多返回条数（默认 20）')
        parser.add_argument('--snippet', type=int, default=300,
                            help='content 预览字数（默认 300）；--full 时忽略')
        parser.add_argument('--full', action='store_true', help='不截断，content 返回全文+原样换行')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        rows = store.search(' '.join(ns.query), tag=ns.tag, limit=ns.limit)
        if not ns.full:
            self._apply_snippet(rows, ns.snippet)
        self._emit(ctx, rows, ns.format)
        return True

    def _get(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt get')
        parser.add_argument('name', help='模板调用键')
        parser.add_argument('--no-count', dest='no_count', action='store_true',
                            help='不计取用次数（纯查看）')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        row = store.find_by_name(ns.name)
        if row is None:
            log.error("未找到 name=%s 的模板（不存在/已删除/他人个人模板）", ns.name)
            return False
        if not ns.no_count:
            store.touch(row['id'])
            row['use_count'] = int(row.get('use_count') or 0) + 1
        self._emit(ctx, [row], ns.format)
        return True

    def _render(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt render')
        parser.add_argument('name', help='模板调用键')
        parser.add_argument('--set', dest='set_items', action='append', default=None,
                            metavar='名=值',
                            help='变量取值，可多次；值含空格/引号时改用 --set-file')
        parser.add_argument('--set-file', dest='set_file', default=None,
                            help='变量取值 JSON 文件（{"k": "v"} 整表；与 --set 合并，--set 优先）')
        parser.add_argument('--with', dest='with_blocks', default=None, metavar='块1,块2',
                            help='强制保留的可选块（逗号分隔，覆盖块标记 default）')
        parser.add_argument('--without', dest='without_blocks', default=None, metavar='块1,块2',
                            help='强制删除的可选块（逗号分隔，覆盖块标记 default）')
        parser.add_argument('--clip', action='store_true',
                            help='渲染结果同时写入系统剪贴板（外送其他 AI 工具）')
        parser.add_argument('--no-count', dest='no_count', action='store_true',
                            help='不计取用次数')
        self._common(parser)
        ns = parser.parse_args(args)

        values: dict[str, Any] = {}
        if ns.set_file is not None:
            raw = _read_text_source(ns.set_file)
            try:
                parsed: dict[str, Any] = json.loads(raw or '')
            except ValueError as e:
                log.error("--set-file 不是合法 JSON: %s", e)
                return False
            if not isinstance(parsed, dict):
                log.error("--set-file 顶层不是 JSON 对象")
                return False
            values.update(parsed)
        extra = _parse_set_items(ns.set_items)
        if extra is None:
            return False
        values.update(extra)
        include = _parse_name_set(ns.with_blocks, '--with')
        exclude = _parse_name_set(ns.without_blocks, '--without')
        if include is None or exclude is None:
            return False

        store = self._build_store(self._resolve_owner(ns))
        row = store.find_by_name(ns.name)
        if row is None:
            log.error("未找到 name=%s 的模板（不存在/已删除/他人个人模板）", ns.name)
            return False
        if not ns.no_count:
            store.touch(row['id'])
        try:
            rendered = render_template(row.get('content') or '', values=values,
                                       meta=row.get('vars'),
                                       include=include, exclude=exclude)
        except ValueError as e:
            log.error("%s", e)
            return False
        ctx.print_delim()
        print(rendered)
        ctx.print_delim()
        if ns.clip and _copy_to_clipboard(rendered):
            log.info("已复制 %d 字到剪贴板", len(rendered))
        return True

    def _update(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt update')
        parser.add_argument('name', help='模板调用键')
        parser.add_argument('--title', default=None)
        parser.add_argument('--description', default=None)
        parser.add_argument('--tags', default=None)
        content_group = parser.add_mutually_exclusive_group(required=False)
        content_group.add_argument('--content', default=None)
        content_group.add_argument('--content-file', dest='content_file', default=None,
                                   help='从 UTF-8 文件读取新正文；"-" 读 stdin（与 --content 互斥）')
        parser.add_argument('--vars-file', dest='vars_file', default=None,
                            help='变量元信息 JSON 文件；正文更新且未提供时按新正文自动重扫')
        self._common(parser, shared=True)
        ns = parser.parse_args(args)

        store = self._build_store(self._resolve_owner(ns))
        before = store.find_by_name(ns.name)
        if before is None:
            log.error("未找到 name=%s 的模板（不存在/已删除/他人个人模板）", ns.name)
            return False

        fields: dict[str, Any] = {}
        content: str | None = None
        if ns.content is not None:
            content = ns.content
        elif ns.content_file is not None:
            content = _read_text_source(ns.content_file)
        if content is not None:
            fields['content'] = content
        for k in ('title', 'description', 'tags'):
            v = getattr(ns, k)
            if v is not None:
                fields[k] = v
        if ns.vars_file is not None:
            raw = _read_text_source(ns.vars_file)
            try:
                parsed: list[Any] = json.loads(raw or '')
            except ValueError as e:
                log.error("--vars-file 不是合法 JSON: %s", e)
                return False
            if not isinstance(parsed, list):
                log.error("--vars-file 顶层不是 JSON 数组")
                return False
            fields['vars_json'] = json.dumps(parsed, ensure_ascii=False)
        elif content is not None:
            # 正文更新未显式给元信息 → 按新正文自动重扫
            fields['vars_json'] = json.dumps(
                [{'name': v, 'required': True} for v in parse_variables(content)],
                ensure_ascii=False)
        if not fields:
            log.error("未指定任何待更新字段")
            return False

        ok = store.update(ns.name, fields, shared_mode=ns.shared)
        if not ok:
            log.error("update %s 未命中：共享模板须加 --shared；他人个人模板不可改", ns.name)
            return False
        after = store.find_by_name(ns.name)
        echo = {'before': {k: before.get(k) for k in
                           ('id', 'name', 'title', 'tags', 'owner', 'updated_by', 'updated_at')},
                'after': {k: (after or {}).get(k) for k in
                          ('id', 'name', 'title', 'tags', 'owner', 'updated_by', 'updated_at')}}
        self._emit(ctx, [echo], 'jsonl')
        return True

    def _forget(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt forget')
        parser.add_argument('name', help='模板调用键')
        self._common(parser, shared=True)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        ok = store.forget(ns.name, shared_mode=ns.shared)
        log.info("forget %s -> %s", ns.name,
                 '已软删除' if ok else '未命中（不存在/已删除/无权限：共享模板须加 --shared）')
        return ok

    def _export(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt export')
        parser.add_argument('name', help='模板调用键')
        parser.add_argument('--out', required=True, help='输出 markdown 文件路径')
        self._common(parser)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        row = store.find_by_name(ns.name)
        if row is None:
            log.error("未找到 name=%s 的模板", ns.name)
            return False
        text = template_to_markdown(row)
        with open(ns.out, 'w', encoding='utf-8') as f:
            f.write(text)
        log.info("已导出 %s -> %s（%d 字）", ns.name, ns.out, len(text))
        return True

    def _import(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt import')
        parser.add_argument('--file', required=True,
                            help='markdown 交换格式文件路径；"-" 读 stdin')
        parser.add_argument('--force', action='store_true',
                            help='同名已存在时覆盖更新')
        self._common(parser, shared=True)
        ns = parser.parse_args(args)
        raw = _read_text_source(ns.file)
        if not raw:
            log.error("--file 读取失败或为空: %s", ns.file)
            return False
        try:
            tpl = markdown_to_template(raw)
        except ValueError as e:
            log.error("解析失败: %s", e)
            return False
        store = self._build_store(self._resolve_owner(ns))
        result = store.save(name=tpl['name'], title=tpl['title'], content=tpl['content'],
                            description=tpl.get('description') or '',
                            tags=tpl.get('tags') or '',
                            vars_meta=tpl.get('vars'), shared=ns.shared, force=ns.force)
        row = result['row']
        log.info("已导入%s name=%s id=%s owner=%s",
                 '（覆盖更新）' if result['action'] == 'updated' else '',
                 tpl['name'], row['id'], row.get('owner') or '共享')
        return True

    def _tags(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt tags')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        self._emit(ctx, store.tag_counts(), ns.format)
        return True

    def _stats(self, ctx: CliContext, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog='python -m baibao agent_prompt stats')
        parser.add_argument('--limit', type=int, default=10, help='最多返回条数（默认 10）')
        self._common(parser, output=True)
        ns = parser.parse_args(args)
        store = self._build_store(self._resolve_owner(ns))
        self._emit(ctx, store.stats(limit=ns.limit), ns.format)
        return True

    # endregion

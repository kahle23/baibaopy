"""
AI Agent prompt 模板库的 RDB 实现（基于 baibao 的 rdb_mgr）。

与 :mod:`baibao.ai_agent.memory` 同构的存储层，管理"给 AI 的任务 prompt 模板"：

  - **全程参数化查询**（``rdb_mgr.query/execute(sql, params)``）——天然防 SQL 注入；
  - **方言自适应**——首次使用时探测目标实例的 ``db_type``，据此选择占位符
    （sqlite 用 ``?``、mysql/postgres 用 ``%s``）与建表 DDL；
  - **幂等建表**——``init_store()`` 用 ``CREATE TABLE IF NOT EXISTS``，重复执行无副作用；
  - **模板正文自描述**——变量 ``{{name}}`` 与可选块 ``<!-- @block:... -->`` 标记均
    定义在正文内，由本模块的纯函数解析（:func:`parse_variables` / :func:`parse_blocks`），
    数据库不冗余存块清单；``vars_json`` 仅存变量的富化元信息（desc/example/required）。

适配哲学（写进技能文档，此处仅提供机制）：占位符只标记必填变量；场景删减靠
可选块的 ``default:on/off`` + 场景注释，AI 语义裁剪（get 后自行判断），
:func:`render_template` 的确定性裁剪仅作外送其他 AI 工具的兜底。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from pykunlun.util import logutil

from baibao.db.rdb import rdb_mgr

log = logutil.getLogger(__name__)

# region ======== 表结构定义（单一信息源）========
#
# 列定义：(列名, MySQL定义, PostgreSQL定义, SQLite定义, 注释)
# 三方言的建表 DDL 由 :meth:`RdbPromptStore._ddl_create_table` 据此生成。

_TABLE_COMMENT = 'AI prompt 模板表（给 AI 的任务 prompt 模板库）'

_COLUMNS: list[tuple[str, str, str, str, str]] = [
    #列名         MySQL                                 PostgreSQL                  SQLite                               注释
    ('id',          'BIGINT AUTO_INCREMENT PRIMARY KEY',  'BIGSERIAL PRIMARY KEY',    'INTEGER PRIMARY KEY AUTOINCREMENT', '主键，自增'),
    ('name',        'VARCHAR(64) NOT NULL',              'VARCHAR(64) NOT NULL',     'TEXT NOT NULL',                     '调用键（英文短名，全局唯一）'),
    ('title',       'VARCHAR(128) NOT NULL',             'VARCHAR(128) NOT NULL',    'TEXT NOT NULL',                     '中文标题'),
    ('description', 'VARCHAR(512)',                      'VARCHAR(512)',             'TEXT',                              '何时用这个模板'),
    ('tags',        "VARCHAR(256) DEFAULT ''",           "VARCHAR(256) DEFAULT ''",  "TEXT DEFAULT ''",                   '逗号分隔的标签'),
    ('content',     'MEDIUMTEXT',                        'TEXT',                     'TEXT',                              '模板正文（含{{var}}与@block标记）'),
    ('vars_json',   'JSON',                              'TEXT',                     'TEXT',                              '变量元信息JSON:[{name,desc,example,required,default}]'),
    ('owner',       'VARCHAR(64)',                       'VARCHAR(64)',              'TEXT',                              '所有者（用户标识，鉴权字段；NULL=共享）'),
    ('created_by',  'VARCHAR(64)',                       'VARCHAR(64)',              'TEXT',                              '创建者'),
    ('created_at',  'DATETIME',                          'TIMESTAMP',                'TEXT',                              '创建时间'),
    ('updated_by',  'VARCHAR(64)',                       'VARCHAR(64)',              'TEXT',                              '最后更新者'),
    ('updated_at',  'DATETIME',                          'TIMESTAMP',                'TEXT',                              '最后更新时间'),
    ('use_count',   'INT DEFAULT 0',                     'INTEGER DEFAULT 0',        'INTEGER DEFAULT 0',                 '取用次数（排序衰减用）'),
    ('last_used_at','DATETIME',                          'TIMESTAMP',                'TEXT',                              '最近一次取用时间'),
    ('is_deleted',  'TINYINT DEFAULT 0',                 'SMALLINT DEFAULT 0',       'INTEGER DEFAULT 0',                 '软删除标记（1/0），forget 仅置 1'),
]


def _sql_str(s: str) -> str:
    """转 SQL 单引号字符串字面量（``'`` → ``''`` 转义）。"""
    return "'" + s.replace("'", "''") + "'"

# endregion


# region ======== 模板正文解析与渲染（纯函数，不依赖 DB）========
#
# 标记语法（详见技能 agent-prompt-library 的 03-schema-and-model.md）：
#   变量：     {{language}}                       —— 占位必填变量，render 时填充
#   可选块头： <!-- @block:concurrent-check | default:off | 仅并发场景保留 -->
#   可选块尾： <!-- @endblock:concurrent-check -->
# 块外内容恒定保留；块内去留由 default 与 with/without 覆盖决定；块不支持嵌套。

_BLOCK_START_RE = re.compile(
    r'<!--\s*@block:([A-Za-z0-9_-]+)((?:\s*\|[^>]*?)?)\s*-->')
_BLOCK_END_RE = re.compile(r'<!--\s*@endblock:([A-Za-z0-9_-]+)\s*-->')
_VAR_RE = re.compile(r'\{\{\s*([A-Za-z0-9_-]+)\s*\}\}')


def parse_variables(content: str) -> list[str]:
    """提取正文中的变量名（``{{name}}``），按首次出现顺序去重。"""
    seen: list[str] = []
    for m in _VAR_RE.finditer(content):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def parse_blocks(content: str) -> list[dict[str, Any]]:
    """
    解析正文中的可选块，返回 ``[{name, default_on, note, start, end}]``。

    ``start``/``end`` 为头/尾标记的 ``(match.start(), match.end())`` 区间。
    校验规则（违反抛 ``ValueError``）：尾标记名与最近头标记不一致、块嵌套、
    有头无尾、有尾无头。
    """
    events: list[tuple[int, int, str, str]] = []  # (pos, end_pos, kind, name)
    for m in _BLOCK_START_RE.finditer(content):
        events.append((m.start(), m.end(), 'start', m.group(1)))
    for m in _BLOCK_END_RE.finditer(content):
        events.append((m.start(), m.end(), 'end', m.group(1)))
    events.sort()

    blocks: list[dict[str, Any]] = []
    stack: list[tuple[int, int, str]] = []
    for pos, end_pos, kind, name in events:
        if kind == 'start':
            if stack:
                raise ValueError(
                    f"可选块不支持嵌套：'{name}' 出现在未闭合的 '{stack[-1][2]}' 内")
            stack.append((pos, end_pos, name))
        else:
            if not stack:
                raise ValueError(f"块尾标记 '@endblock:{name}' 没有对应的头标记")
            spos, send, sname = stack.pop()
            if sname != name:
                raise ValueError(
                    f"块标记不匹配：头 '@block:{sname}' 对应的尾标记是 '@endblock:{name}'")
            blocks.append({'name': name, 'start': (spos, send), 'end': (pos, end_pos),
                           'default_on': True, 'note': ''})
    if stack:
        raise ValueError(f"块 '@block:{stack[-1][2]}' 缺少尾标记 @endblock")

    # 头标记属性：| default:on/off | 场景注释（可多段，非 default: 前缀的都进 note）
    for b in blocks:
        attrs = _BLOCK_START_RE.match(content[b['start'][0]:b['start'][1]])
        # match 必然成功（区间即由该正则产生）
        assert attrs is not None
        raw = attrs.group(2) or ''
        notes: list[str] = []
        for part in raw.split('|'):
            part = part.strip()
            if not part:
                continue
            low = part.lower()
            if low in ('default:on', 'default:on=true', 'default:true'):
                b['default_on'] = True
            elif low in ('default:off', 'default:off=false', 'default:false'):
                b['default_on'] = False
            else:
                notes.append(part)
        b['note'] = '；'.join(notes)
    return blocks


def render_template(
    content: str,
    values: dict[str, Any] | None = None,
    meta: list[dict[str, Any]] | None = None,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
) -> str:
    """
    确定性渲染：填变量 → 按开关裁剪块并剥离标记 → 折叠多余空行。

    Args:
        content: 模板正文（含标记）。
        values: 变量取值表；未提供的必填变量在渲染完成后统一抛 ``ValueError`` 列出。
        meta: 变量元信息（save 时落库的 vars_json），支持 ``required=False`` + ``default``。
        include/exclude: 块名集合，覆盖块标记里的 default；同名同时出现在两集合报错。
            未指定的块按标记 default 取舍，include 的块剥离标记保留内容，exclude 的整块删除。
    """
    values = values or {}
    meta_by_name = {m.get('name'): m for m in (meta or []) if m.get('name')}
    include = include or set()
    exclude = exclude or set()
    conflict = include & exclude
    if conflict:
        raise ValueError(f"块同时出现在 include/exclude: {', '.join(sorted(conflict))}")

    blocks = parse_blocks(content)
    known = {b['name'] for b in blocks}
    unknown = (include | exclude) - known
    if unknown:
        raise ValueError(
            f"未知的块名: {', '.join(sorted(unknown))}；正文中的可选块: {', '.join(sorted(known)) or '（无）'}")

    missing: list[str] = []

    def _fill(text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            if name in values:
                return str(values[name])
            m0 = meta_by_name.get(name)
            if m0 is not None and not m0.get('required', True):
                if 'default' in m0:
                    return str(m0['default'])
                return m.group(0)  # 可选且无默认：保留占位符原样
            missing.append(name)
            return m.group(0)
        return _VAR_RE.sub(repl, text)

    parts: list[str] = []
    pos = 0
    for b in blocks:
        parts.append(_fill(content[pos:b['start'][0]]))
        keep = b['name'] not in exclude and (b['name'] in include or b['default_on'])
        if keep:
            parts.append(_fill(content[b['start'][1]:b['end'][0]]))
        pos = b['end'][1]
    parts.append(_fill(content[pos:]))
    if missing:
        uniq = sorted(set(missing))
        raise ValueError(f"缺少必填变量: {', '.join(uniq)}（用 --set 名=值 提供）")
    out = ''.join(parts)
    # 删除块后可能留下连续空行，统一折叠为最多 1 个空行
    return re.sub(r'\n{3,}', '\n\n', out)

# endregion


# region ======== markdown 交换格式（export/import 用，纯函数）========
#
# 格式：frontmatter（name/title/description/tags[/vars] 单行 key: value）+ 空行 + 正文。
# 与人手写的模板文件兼容；vars 行为单行 JSON（可省略）。

def template_to_markdown(row: dict[str, Any]) -> str:
    """把模板行（含 vars 解析结果）导出为 markdown 交换格式文本。"""
    lines = ['---']
    for k in ('name', 'title', 'description', 'tags'):
        v = row.get(k) or ''
        lines.append(f'{k}: {str(v).replace(chr(10), " ").strip()}')
    vars_meta = row.get('vars')
    if vars_meta:
        lines.append('vars: ' + json.dumps(vars_meta, ensure_ascii=False))
    lines.append('---')
    return '\n'.join(lines) + '\n' + (row.get('content') or '')


def markdown_to_template(text: str) -> dict[str, Any]:
    """
    解析 markdown 交换格式为模板字段 dict（name/title/description/tags/vars/content）。

    frontmatter 缺 name/title 抛 ``ValueError``；vars 行非法 JSON 时忽略并告警。
    """
    text = text.lstrip('\ufeff \t\n')
    if not text.startswith('---'):
        raise ValueError('缺少 frontmatter 头（首行应为 ---）')
    close = text.find('\n---', 3)
    if close < 0:
        raise ValueError('frontmatter 未闭合（缺第二个 --- 行）')
    header = text[3:close]
    result: dict[str, Any] = {'vars': None}
    for line in header.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        k, _, v = line.partition(':')
        k, v = k.strip(), v.strip()
        if k in ('name', 'title', 'description', 'tags'):
            result[k] = v
        elif k == 'vars':
            try:
                parsed = json.loads(v)
                result['vars'] = parsed if isinstance(parsed, list) else None
            except ValueError:
                log.warning("vars 行不是合法 JSON，已忽略: %s", v[:80])
    if not result.get('name') or not result.get('title'):
        raise ValueError('frontmatter 缺少必填的 name 或 title')
    result['content'] = text[close + 4:].lstrip('\n')
    return result

# endregion


def _jdump(obj: Any) -> str | None:
    """序列化为 JSON 字符串（ensure_ascii=False，None 透传）。"""
    return None if obj is None else json.dumps(obj, ensure_ascii=False, default=str)


def _jload(text: Any) -> Any:
    """反序列化 JSON（容错：None/非法返回 None）。"""
    if not text:
        return None
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return None


class RdbPromptStore:
    """
    基于 rdb_mgr 的 prompt 模板存储实现。

    通过指定 rdb 实例名（``db_name``）复用 baibao 已注册的数据库连接，默认表名
    ``ai_prompt_template``。owner 语义与 :class:`~baibao.ai_agent.memory.RdbMemoryStore`
    一致：NULL=共享（所有人可见）、有值=个人（仅本人可见）。

    Args:
        db_name: rdb 实例名；None 用 rdb 默认实例。
        owner: 当前身份（用户标识）；None 表示无身份（仅共享域可见）。
        table: 模板表名（默认 ``ai_prompt_template``）。
    """

    backend_type = 'rdb'

    #: update 可白名单更新的字段
    UPDATABLE_FIELDS = ('title', 'description', 'tags', 'content', 'vars_json')

    def __init__(self, db_name: str | None = None, owner: str | None = None,
                 table: str = 'ai_prompt_template') -> None:
        self._db_name = db_name
        self._owner = owner if (owner is not None and owner.strip()) else None
        self._table = table
        self._db_type: str | None = None  # 懒加载并缓存

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def db_name(self) -> str | None:
        return self._db_name

    @property
    def table(self) -> str:
        return self._table

    # region ======== 方言自适应 ========

    def _get_db_type(self) -> str:
        """探测并缓存目标实例的数据库类型标识。"""
        if self._db_type is None:
            self._db_type = rdb_mgr.get_client(self._db_name).db_type
        return self._db_type

    def _ph(self) -> str:
        """返回当前方言的参数占位符：sqlite 用 ``?``，其余用 ``%s``。"""
        return '?' if self._get_db_type() == 'sqlite' else '%s'

    def _vis_clause(self) -> tuple[str, list[Any]]:
        """读可见性：共享（owner IS NULL）+ 本人个人模板；无身份则仅共享。"""
        ph = self._ph()
        if self._owner is None:
            return 'owner IS NULL', []
        return f'(owner IS NULL OR owner = {ph})', [self._owner]

    def _perm_clause(self, shared_mode: bool) -> tuple[str, list[Any]]:
        """写权限：shared_mode 限共享数据（owner IS NULL）；否则限本人数据。"""
        ph = self._ph()
        if shared_mode or self._owner is None:
            return 'owner IS NULL', []
        return f'owner = {ph}', [self._owner]

    # endregion

    # region ======== 建表 DDL ========

    def init_store(self) -> None:
        """幂等建表 + 唯一索引（按目标方言）。"""
        db_type = self._get_db_type()
        rdb_mgr.execute(self._ddl_create_table(db_type), name=self._db_name)
        for stmt in self._ddl_indexes(db_type):
            rdb_mgr.execute(stmt, name=self._db_name)
        log.info("RdbPromptStore 已初始化表 %s (db_type=%s, db_name=%s)",
                 self._table, db_type, self._db_name)

    def _ddl_create_table(self, db_type: str) -> str:
        """按方言返回建表语句（仅 CREATE TABLE）。"""
        t = self._table
        if db_type == 'mysql':
            col_lines = [f'    {c[0]} {c[1]} COMMENT {_sql_str(c[4])}' for c in _COLUMNS]
            idx_lines = [f'    UNIQUE KEY uk_{t}_name (name)']
            body = ',\n'.join(col_lines + idx_lines)
            return (f'CREATE TABLE IF NOT EXISTS {t} (\n{body}\n) '
                    f'CHARACTER SET utf8mb4 COMMENT={_sql_str(_TABLE_COMMENT)}')
        if db_type == 'postgresql':
            col_lines = [f'    {c[0]} {c[2]}' for c in _COLUMNS]
            body = ',\n'.join(col_lines)
            return f'CREATE TABLE IF NOT EXISTS {t} (\n{body}\n)'
        # 默认 SQLite
        col_lines = [f'    {c[0]} {c[3]}' for c in _COLUMNS]
        body = ',\n'.join(col_lines)
        return f'CREATE TABLE IF NOT EXISTS {t} (\n{body}\n)'

    def _ddl_indexes(self, db_type: str) -> list[str]:
        """按方言返回索引语句列表（MySQL 的唯一索引已内联在 CREATE TABLE 内）。"""
        t = self._table
        if db_type == 'mysql':
            return []
        return [f'CREATE UNIQUE INDEX IF NOT EXISTS uk_{t}_name ON {t} (name)']

    # endregion

    # region ======== 写操作 ========

    def save(
        self,
        name: str,
        title: str,
        content: str,
        description: str = '',
        tags: str = '',
        vars_meta: list[dict[str, Any]] | None = None,
        shared: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        新增（或 ``--force`` 覆盖更新）一条模板，返回 ``{'action': 'inserted'|'updated', 'row': ...}``。

        - 同名查重：name 全局唯一（含他人可见范围），命中且无 ``force`` 抛 ``ValueError``
          并附已有 id；有 ``force`` 走 update 语义（保留 id/owner/created_*，刷新 updated_*）。
        - 身份盖章：``shared=True`` 落共享（owner=NULL）；否则落个人（owner=当前身份，
          无身份时抛 ``ValueError``，防止"个人默认值静默落共享"的陷阱）。
        - 块标记强校验：写入前 :func:`parse_blocks` 解析正文，坏标记（未闭合/嵌套/
          头尾不匹配）当场抛 ``ValueError``，不留到 render 时才暴露。
        - ``vars_meta`` 未提供时按正文自动扫描生成 ``[{name, required: True}]``。
        """
        parse_blocks(content)
        if not shared and self._owner is None:
            raise ValueError(
                '个人模板需要身份（--owner / 环境变量 AGENT_PROMPT_OWNER / 配置文件 owner）；'
                '要存共享库请显式加 --shared')
        if vars_meta is None:
            vars_meta = [{'name': v, 'required': True} for v in parse_variables(content)]
        owner_val: str | None = None if shared else self._owner
        vars_json = _jdump(vars_meta)
        existing = self.find_by_name(name, include_deleted=False, any_owner=True)

        if existing is not None:
            if not force:
                raise ValueError(
                    f"同名模板已存在 id={existing['id']} title={existing['title']!r} "
                    f"owner={existing['owner'] or '共享'}；覆盖更新加 --force，或换名")
            # 覆盖更新按目标行归属鉴权：共享行须显式 --shared；个人行仅本人可覆盖
            if not ((existing['owner'] is None and shared)
                    or (existing['owner'] is not None and existing['owner'] == self._owner)):
                raise ValueError(
                    f"无权覆盖 id={existing['id']}（owner={existing['owner'] or '共享'}）："
                    f"共享模板须显式 --shared，他人个人模板不可覆盖")
            ph = self._ph()
            sets = [f'{k} = {ph}' for k in ('title', 'description', 'tags', 'content', 'vars_json')]
            params: list[Any] = [title, description, tags, content, vars_json,
                                 self._owner, datetime.now(), name]
            sql = (f'UPDATE {self._table} SET {", ".join(sets)}, '
                   f'updated_by = {ph}, updated_at = {ph} '
                   f'WHERE name = {ph} AND is_deleted = 0')
            rdb_mgr.execute(sql, tuple(params), name=self._db_name)
            row = self.find_by_name(name, include_deleted=False, any_owner=True)
            assert row is not None
            return {'action': 'updated', 'row': row}

        now = datetime.now()
        ph = self._ph()
        cols = ('name, title, description, tags, content, vars_json, owner, '
                'created_by, created_at, updated_by, updated_at, use_count, '
                'last_used_at, is_deleted')
        placeholders = ', '.join([ph] * 14)
        sql = f'INSERT INTO {self._table} ({cols}) VALUES ({placeholders})'
        ins_params: tuple[Any, ...] = (name, title, description, tags, content, vars_json, owner_val,
                                       self._owner, now, self._owner, now, 0, None, 0)

        if self._get_db_type() == 'postgresql':
            rows = rdb_mgr.query(sql + ' RETURNING id', ins_params, name=self._db_name)
            new_id = int(rows[0]['id'])
        else:
            # mysql / sqlite：execute 不回 lastrowid，走裸连接取（多人并发的同名竞态由唯一索引兜底）
            conn = rdb_mgr.get_connection(self._db_name)
            cur = None
            try:
                cur = conn.cursor()
                cur.execute(sql, ins_params)
                conn.commit()
                lastrowid = cur.lastrowid
                if lastrowid is None:
                    raise RuntimeError('INSERT 未返回 lastrowid（非预期，请检查表结构）')
                new_id = int(lastrowid)
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    log.warning('save 回滚失败', exc_info=True)
                if 'IntegrityError' in type(e).__name__ or 'Duplicate' in str(e):
                    raise ValueError(f'同名模板刚被其他人创建：{name}') from e
                raise
            finally:
                if cur:
                    cur.close()
                conn.close()
        row = self.get_by_id(new_id)
        assert row is not None
        return {'action': 'inserted', 'row': row}

    def update(self, name: str, fields: dict[str, Any], shared_mode: bool = False) -> bool:
        """按 name 部分更新（仅白名单字段，须通过权限校验）。返回是否命中。

        更新正文时与 :meth:`save` 一致，写入前用 :func:`parse_blocks` 强校验块标记。
        """
        allowed = {k: v for k, v in fields.items() if k in self.UPDATABLE_FIELDS}
        if not allowed:
            return False
        if 'content' in allowed:
            parse_blocks(allowed['content'])
        ph = self._ph()
        perm_sql, perm_params = self._perm_clause(shared_mode)
        set_parts = [f'{k} = {ph}' for k in allowed]
        params: list[Any] = list(allowed.values())
        set_parts.append(f'updated_by = {ph}')
        params.append(self._owner)
        set_parts.append(f'updated_at = {ph}')
        params.append(datetime.now())
        params.append(name)
        params.extend(perm_params)
        sql = (f'UPDATE {self._table} SET {", ".join(set_parts)} '
               f'WHERE name = {ph} AND is_deleted = 0 AND {perm_sql}')
        affected = rdb_mgr.execute(sql, tuple(params), name=self._db_name)
        return affected > 0

    def forget(self, name: str, shared_mode: bool = False) -> bool:
        """软删除（is_deleted=1），权限规则同 update。返回是否命中。"""
        ph = self._ph()
        perm_sql, perm_params = self._perm_clause(shared_mode)
        params: list[Any] = [datetime.now(), name]
        params.extend(perm_params)
        sql = (f'UPDATE {self._table} SET is_deleted = 1, updated_at = {ph} '
               f'WHERE name = {ph} AND is_deleted = 0 AND {perm_sql}')
        affected = rdb_mgr.execute(sql, tuple(params), name=self._db_name)
        return affected > 0

    def touch(self, id_: int) -> None:
        """取用计数 +1、刷新 last_used_at（get 命中副作用，不做鉴权）。"""
        ph = self._ph()
        sql = (f'UPDATE {self._table} SET use_count = use_count + 1, last_used_at = {ph} '
               f'WHERE id = {ph}')
        rdb_mgr.execute(sql, (datetime.now(), id_), name=self._db_name)

    # endregion

    # region ======== 读操作 ========

    @staticmethod
    def _attach_parsed(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """给行附上解析结果：vars（元信息列表）与 blocks（块清单）。"""
        if row is None:
            return None
        row = dict(row)
        vars_meta = _jload(row.get('vars_json'))
        row['vars'] = vars_meta if isinstance(vars_meta, list) else []
        try:
            row['blocks'] = [
                {'name': b['name'], 'default_on': b['default_on'], 'note': b['note']}
                for b in parse_blocks(row.get('content') or '')]
        except ValueError as e:
            row['blocks'] = [{'parse_error': str(e)}]
        return row

    def get_by_id(self, id_: int) -> dict[str, Any] | None:
        ph = self._ph()
        vis_sql, vis_params = self._vis_clause()
        params: list[Any] = [id_]
        params.extend(vis_params)
        sql = (f'SELECT * FROM {self._table} '
               f'WHERE id = {ph} AND is_deleted = 0 AND {vis_sql}')
        rows = rdb_mgr.query(sql, tuple(params), name=self._db_name)
        return self._attach_parsed(rows[0]) if rows else None

    def find_by_name(self, name: str, include_deleted: bool = False,
                     any_owner: bool = False) -> dict[str, Any] | None:
        """按 name 精确取单条。``any_owner=True`` 跳过可见性（save 查重用）。"""
        ph = self._ph()
        params: list[Any] = [name]
        where = [f'name = {ph}']
        if not include_deleted:
            where.append('is_deleted = 0')
        if not any_owner:
            vis_sql, vis_params = self._vis_clause()
            where.append(vis_sql)
            params.extend(vis_params)
        sql = f'SELECT * FROM {self._table} WHERE {" AND ".join(where)}'
        rows = rdb_mgr.query(sql, tuple(params), name=self._db_name)
        return self._attach_parsed(rows[0]) if rows else None

    def list_templates(self, tag: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """浏览（按取用热度排序，不计数）。tag 为子串匹配。"""
        ph = self._ph()
        vis_sql, vis_params = self._vis_clause()
        params: list[Any] = list(vis_params)
        where = [vis_sql, 'is_deleted = 0']
        if tag:
            where.append(f'tags LIKE {ph}')
            params.append(f'%{tag}%')
        sql = (f'SELECT * FROM {self._table} WHERE {" AND ".join(where)} '
               f'ORDER BY use_count DESC, last_used_at DESC LIMIT {ph}')
        params.append(limit)
        return rdb_mgr.query(sql, tuple(params), name=self._db_name)

    def search(self, query: str, tag: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """
        模糊检索：多关键词 × (name/title/tags/description/content) 的 CASE WHEN 加权
        求和为 ``_score``（name 4 / title 3 / tags 2 / description 2 / content 1），
        WHERE 为各关键词的多字段 OR 组，按 _score → use_count → last_used_at 排序。
        """
        ph = self._ph()
        tokens = [t for t in query.split() if t]
        params: list[Any] = []
        if tokens:
            score_terms: list[str] = []
            for tk in tokens:
                score_terms.append(
                    f'(CASE WHEN name LIKE {ph} THEN 4 ELSE 0 END'
                    f' + CASE WHEN title LIKE {ph} THEN 3 ELSE 0 END'
                    f' + CASE WHEN tags LIKE {ph} THEN 2 ELSE 0 END'
                    f' + CASE WHEN description LIKE {ph} THEN 2 ELSE 0 END'
                    f' + CASE WHEN content LIKE {ph} THEN 1 ELSE 0 END)')
                like = f'%{tk}%'
                params.extend([like] * 5)
            score_expr = '(' + ' + '.join(score_terms) + ')'
        else:
            score_expr = '0'

        where_parts: list[str] = []
        if tokens:
            or_groups: list[str] = []
            for tk in tokens:
                or_groups.append(
                    f'(name LIKE {ph} OR title LIKE {ph} OR tags LIKE {ph} '
                    f'OR description LIKE {ph} OR content LIKE {ph})')
                like = f'%{tk}%'
                params.extend([like] * 5)
            where_parts.append('(' + ' OR '.join(or_groups) + ')')
        where_parts.append('is_deleted = 0')
        vis_sql, vis_params = self._vis_clause()
        where_parts.append(vis_sql)
        params.extend(vis_params)
        if tag:
            where_parts.append(f'tags LIKE {ph}')
            params.append(f'%{tag}%')

        sql = (f'SELECT *, {score_expr} AS _score FROM {self._table}'
               f' WHERE {" AND ".join(where_parts)}'
               f' ORDER BY _score DESC, use_count DESC, last_used_at DESC'
               f' LIMIT {ph}')
        params.append(limit)
        return rdb_mgr.query(sql, tuple(params), name=self._db_name)

    def stats(self, limit: int = 10) -> list[dict[str, Any]]:
        """使用排行（含软删除外的全部，按 use_count → last_used_at 排序）。"""
        ph = self._ph()
        sql = (f'SELECT name, title, owner, use_count, last_used_at FROM {self._table} '
               f'WHERE is_deleted = 0 ORDER BY use_count DESC, last_used_at DESC LIMIT {ph}')
        return rdb_mgr.query(sql, (limit,), name=self._db_name)

    def tag_counts(self) -> list[dict[str, Any]]:
        """标签聚合（Python 侧分词计数，个人规模足够）。"""
        vis_sql, vis_params = self._vis_clause()
        sql = f'SELECT tags FROM {self._table} WHERE is_deleted = 0 AND {vis_sql}'
        rows = rdb_mgr.query(sql, tuple(vis_params), name=self._db_name)
        counter: dict[str, int] = {}
        for r in rows:
            for t in (r.get('tags') or '').split(','):
                t = t.strip()
                if t:
                    counter[t] = counter.get(t, 0) + 1
        return [{'tag': k, 'count': v} for k, v in
                sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    def count(self, include_deleted: bool = False) -> int:
        vis_sql, vis_params = self._vis_clause()
        params: list[Any] = list(vis_params)
        clauses = [vis_sql]
        if not include_deleted:
            clauses.append('is_deleted = 0')
        sql = f'SELECT COUNT(*) AS cnt FROM {self._table} WHERE {" AND ".join(clauses)}'
        rows = rdb_mgr.query(sql, tuple(params), name=self._db_name)
        return int(rows[0]['cnt']) if rows else 0

    # endregion


__all__ = [
    'RdbPromptStore',
    'markdown_to_template',
    'parse_blocks',
    'parse_variables',
    'render_template',
    'template_to_markdown',
]

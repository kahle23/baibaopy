"""
AI Agent 记忆能力的 RDB 实现（基于 baibao 的 rdb_mgr）。

对标 ``baibao.db.rdb`` 的驱动实现层：复用 :class:`~baibao.db.rdb.rdb_mgr` 提供的
跨数据库（MySQL / PostgreSQL / SQLite）查询与执行能力，实现
:class:`~pykunlun.ai_agent.memory.MemoryStore` 抽象。

核心要点：

  - **全程参数化查询**（``rdb_mgr.query/execute(sql, params)``）——天然防 SQL 注入，
    调用方无需手动转义；
  - **方言自适应**——首次使用时探测目标实例的 ``db_type``，据此选择占位符
    （sqlite 用 ``?``、mysql/postgres 用 ``%s``）、自增主键与建表 DDL；
  - **幂等建表**——``init_store()`` 用 ``CREATE TABLE IF NOT EXISTS``，重复执行无副作用；
  - **remember 回填 id**——因 ``RdbClient.execute`` 只返回受影响行数，此处对
    postgres 用 ``INSERT ... RETURNING id``，对 mysql/sqlite 走裸连接读 ``lastrowid``。
"""

from datetime import datetime
from typing import Any

from pykunlun.ai_agent.memory import (
    UPDATABLE_FIELDS,
    MemoryManager,
    MemoryRecord,
    MemoryStore,
    permission_clause,
    tokenize_query,
    visibility_clause,
)
from pykunlun.util import logutil

from baibao.db.rdb import rdb_mgr

log = logutil.getLogger(__name__)


# region ======== 表结构定义（单一信息源）========
#
# 列定义：(列名, MySQL定义, PostgreSQL定义, SQLite定义, 注释)
# 三方言的建表 DDL 由 :func:`RdbMemoryStore._ddl` 据此生成，避免列清单重复维护。
# 注释：MySQL 内联 ``COMMENT``、PostgreSQL 用 ``COMMENT ON``，SQLite 无原生注释支持。

_TABLE_COMMENT = 'AI 记忆表（事实类项目知识）'

_COLUMNS: list[tuple[str, str, str, str, str]] = [
    #列名           MySQL                                  PostgreSQL                       SQLite                              注释
    ('id',          'BIGINT AUTO_INCREMENT PRIMARY KEY',   'BIGSERIAL PRIMARY KEY',         'INTEGER PRIMARY KEY AUTOINCREMENT', '主键，自增'),
    ('scope',       'VARCHAR(64) NOT NULL',               'VARCHAR(64) NOT NULL',          'TEXT NOT NULL',                     '作用域（项目名/模块名）'),
    ('category',    'VARCHAR(32) NOT NULL',               'VARCHAR(32) NOT NULL',          'TEXT NOT NULL',                     '事实类型（file-path/convention/decision/quirk/no-go/history/other）'),
    ('title',       'VARCHAR(255) NOT NULL',              'VARCHAR(255) NOT NULL',         'TEXT NOT NULL',                     '一句话摘要（检索主命中点）'),
    ('content',     'TEXT',                               'TEXT',                          'TEXT',                              '完整内容'),
    ('owner',       'VARCHAR(64)',                        'VARCHAR(64)',                   'TEXT',                              '所有者（用户标识，鉴权字段；NULL=共享）'),
    ('owner_group', 'VARCHAR(64)',                        'VARCHAR(64)',                   'TEXT',                              '所有者团队/组（标签，不参与鉴权）'),
    ('machine',     'VARCHAR(64)',                        'VARCHAR(64)',                   'TEXT',                              '物理机标识（标签，不参与鉴权；多机隔离用）'),
    ('agent_name',  'VARCHAR(64)',                        'VARCHAR(64)',                   'TEXT',                              '沉淀本条的 agent 外壳标识（标签，不参与鉴权）'),
    ('keywords',    "VARCHAR(255) DEFAULT ''",            "VARCHAR(255) DEFAULT ''",       "TEXT DEFAULT ''",                  '逗号分隔的关键词/标签'),
    ('source',      "VARCHAR(32) DEFAULT 'user-told'",    "VARCHAR(32) DEFAULT 'user-told'", "TEXT DEFAULT 'user-told'",        '来源（user-told/code-derived/inferred）'),
    ('confidence',  'TINYINT DEFAULT 80',                 'SMALLINT DEFAULT 80',           'INTEGER DEFAULT 80',                '置信度 0~100'),
    ('pinned',      'TINYINT DEFAULT 0',                  'SMALLINT DEFAULT 0',            'INTEGER DEFAULT 0',                 '是否置顶（1/0），置顶项召回排最前'),
    ('use_count',   'INT DEFAULT 0',                      'INTEGER DEFAULT 0',             'INTEGER DEFAULT 0',                 '命中次数（排序衰减用）'),
    ('last_used_at','DATETIME',                           'TIMESTAMP',                     'TEXT',                              '最近一次命中时间'),
    ('is_deleted',  'TINYINT DEFAULT 0',                  'SMALLINT DEFAULT 0',            'INTEGER DEFAULT 0',                 '软删除标记（1/0），forget 仅置 1'),
    ('created_at',  'DATETIME',                           'TIMESTAMP',                     'TEXT',                              '创建时间'),
    ('updated_at',  'DATETIME',                           'TIMESTAMP',                     'TEXT',                              '更新时间'),
]


def _sql_str(s: str) -> str:
    """转 SQL 单引号字符串字面量（``'`` → ``''`` 转义）。"""
    return "'" + s.replace("'", "''") + "'"

# endregion


class RdbMemoryStore(MemoryStore):
    """
    基于 rdb_mgr 的记忆存储实现。

    通过指定 rdb 实例名（``db_name``）复用 baibao 已注册的数据库连接；
    省略时使用 rdb 的默认实例（``default``）。建议为记忆库单独注册一个别名
    ``memory`` 的实例，与业务库隔离。

    表结构（默认表名 ``ai_memory``，可经 ``table`` 自定义）见 :meth:`_ddl`，
    字段与 :class:`~pykunlun.ai_agent.memory.MemoryRecord` 一一对应。

    Args:
        db_name: rdb 实例名；None 用 rdb 默认实例。
        owner: 当前身份（用户标识）；None 表示无身份（天然处于共享域）。
        owner_group: 当前团队/组（标签，仅 remember 盖章用，不参与鉴权）。
        machine: 当前物理机标识（标签，仅 remember 盖章 + machine_bound 去重用，不参与鉴权）。
        agent_name: 当前 agent 外壳标识（标签，仅 remember 盖章用，不参与鉴权）。
        table: 记忆表名（默认 ``ai_memory``）。
    """

    backend_type = 'rdb'

    def __init__(
        self,
        db_name: str | None = None,
        owner: str | None = None,
        owner_group: str | None = None,
        machine: str | None = None,
        agent_name: str | None = None,
        table: str = 'ai_memory',
    ) -> None:
        self._db_name = db_name
        self._owner = owner if (owner is not None and owner.strip()) else None
        self._owner_group = owner_group
        self._machine = machine if (machine is not None and machine.strip()) else None
        self._agent_name = agent_name if (agent_name is not None and agent_name.strip()) else None
        self._table = table
        self._db_type: str | None = None  # 懒加载并缓存

    @property
    def db_name(self) -> str | None:
        return self._db_name

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def owner_group(self) -> str | None:
        return self._owner_group

    @property
    def machine(self) -> str | None:
        return self._machine

    @property
    def agent_name(self) -> str | None:
        return self._agent_name

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

    # endregion

    # region ======== 建表 DDL ========

    def init_store(self) -> None:
        """幂等建表 + 索引（按目标方言）。

        顺序：建表 → 老库补列 → 索引。索引必须在补列之后（pg/sqlite 的
        ``CREATE INDEX ... (machine, ...)`` 依赖 machine 列已存在）。
        """
        db_type = self._get_db_type()
        rdb_mgr.execute(self._ddl_create_table(db_type), name=self._db_name)
        self._migrate(db_type)  # 老库补列；新库本就有，幂等
        for stmt in self._ddl_indexes(db_type):
            rdb_mgr.execute(stmt, name=self._db_name)
        log.info("RdbMemoryStore 已初始化表 %s (db_type=%s, db_name=%s)",
                 self._table, db_type, self._db_name)

    def _migrate(self, db_type: str) -> None:
        """幂等迁移：补齐老库缺失的 machine/agent_name 列。

        CREATE TABLE IF NOT EXISTS 对已存在的表是 noop，故老库（无新列）需靠此方法补列。
        按方言查列名：SQLite 用 PRAGMA、MySQL/PostgreSQL 用 information_schema。
        """
        t = self._table
        if db_type == 'sqlite':
            rows = rdb_mgr.query(f'PRAGMA table_info({t})', name=self._db_name)
            existing = {r['name'] for r in rows}
        else:
            # MySQL / PostgreSQL：schema 用当前库的；表名可能含大小写，按 lower 匹配
            rows = rdb_mgr.query(
                'SELECT column_name AS name FROM information_schema.columns '
                'WHERE table_name = %s',
                (t,), name=self._db_name)
            existing = {r['name'] for r in rows}
        col_type = 'TEXT' if db_type == 'sqlite' else 'VARCHAR(64)'
        if 'machine' not in existing:
            rdb_mgr.execute(f'ALTER TABLE {t} ADD COLUMN machine {col_type}', name=self._db_name)
        if 'agent_name' not in existing:
            rdb_mgr.execute(f'ALTER TABLE {t} ADD COLUMN agent_name {col_type}', name=self._db_name)

    def _ddl_create_table(self, db_type: str) -> str:
        """按方言返回建表语句（仅 CREATE TABLE，不含索引）。

        - MySQL：列与表注释内联 ``COMMENT``（索引由 :meth:`_ddl_indexes` 经内联方式
          附加——MySQL 索引只能内联在 CREATE TABLE 内，故新库才有全部索引；
          老库靠 :meth:`_migrate` 只补列，索引缺失可接受）。
        - PostgreSQL / SQLite：仅列定义，索引走单独的 :meth:`_ddl_indexes`。
        """
        t = self._table
        if db_type == 'mysql':
            col_lines = [f'    {c[0]} {c[1]} COMMENT {_sql_str(c[4])}' for c in _COLUMNS]
            idx_lines = [
                f'    INDEX idx_{t}_scope_del (scope, is_deleted)',
                f'    INDEX idx_{t}_scope_cat (scope, category)',
                f'    INDEX idx_{t}_owner_del (owner, is_deleted)',
                f'    INDEX idx_{t}_machine_del (machine, is_deleted)',
            ]
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
        """按方言返回索引语句列表（在 :meth:`_migrate` 之后执行）。

        - MySQL：空（索引已内联在 CREATE TABLE 内，无法单独追加；老库索引缺失可接受）。
        - PostgreSQL：CREATE INDEX + COMMENT ON。
        - SQLite：CREATE INDEX（无注释）。
        """
        t = self._table
        if db_type == 'mysql':
            return []
        stmts = [
            f'CREATE INDEX IF NOT EXISTS idx_{t}_scope_del ON {t} (scope, is_deleted)',
            f'CREATE INDEX IF NOT EXISTS idx_{t}_scope_cat ON {t} (scope, category)',
            f'CREATE INDEX IF NOT EXISTS idx_{t}_owner_del ON {t} (owner, is_deleted)',
            f'CREATE INDEX IF NOT EXISTS idx_{t}_machine_del ON {t} (machine, is_deleted)',
        ]
        if db_type == 'postgresql':
            stmts.append(f'COMMENT ON TABLE {t} IS {_sql_str(_TABLE_COMMENT)}')
            for c in _COLUMNS:
                stmts.append(f'COMMENT ON COLUMN {t}.{c[0]} IS {_sql_str(c[4])}')
        return stmts

    # endregion

    # region ======== 写操作 ========

    def remember(self, record: MemoryRecord, shared_mode: bool = False) -> int:
        """插入一条记忆，回填并返回新 id（参数化，防注入）。"""
        now = datetime.now()
        db_type = self._get_db_type()
        ph = self._ph()
        # 盖章：owner_group 标签；owner 按角色（共享角色置空）
        if record.owner_group is None:
            record.owner_group = self._owner_group
        if shared_mode:
            record.owner = None
        elif record.owner is None:
            record.owner = self._owner
        # machine / agent_name：始终盖当前绑定值（record 显式给非 None 时保留之）
        if record.machine is None:
            record.machine = self._machine
        if record.agent_name is None:
            record.agent_name = self._agent_name
        cols = ('scope, category, title, content, owner, owner_group, machine, agent_name, '
                'keywords, source, confidence, pinned, use_count, last_used_at, is_deleted, '
                'created_at, updated_at')
        placeholders = ', '.join([ph] * 17)
        sql = f'INSERT INTO {self._table} ({cols}) VALUES ({placeholders})'
        params: tuple[Any, ...] = (
            record.scope, record.category, record.title, record.content,
            record.owner, record.owner_group, record.machine, record.agent_name,
            record.keywords, record.source, record.confidence, record.pinned,
            record.use_count, record.last_used_at or now, record.is_deleted, now, now,
        )

        if db_type == 'postgresql':
            rows = rdb_mgr.query(sql + ' RETURNING id', params, name=self._db_name)
            return int(rows[0]['id'])

        # mysql / sqlite：execute 不回 lastrowid，走裸连接取
        conn = rdb_mgr.get_connection(self._db_name)
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            lastrowid = cur.lastrowid
            if lastrowid is None:
                raise RuntimeError("INSERT 未返回 lastrowid（非预期，请检查表结构）")
            return int(lastrowid)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                log.warning("remember 回滚失败", exc_info=True)
            raise
        finally:
            if cur:
                cur.close()
            conn.close()

    def update(self, id: int, fields: dict[str, Any], shared_mode: bool = False) -> bool:
        """按 id 部分更新（仅白名单字段，须通过权限校验）。返回是否命中。"""
        allowed = {k: v for k, v in fields.items() if k in UPDATABLE_FIELDS}
        if not allowed:
            return False
        ph = self._ph()
        perm_sql, perm_params = permission_clause(shared_mode, self._owner, ph)
        set_parts = [f'{k} = {ph}' for k in allowed]
        params: list[Any] = list(allowed.values())
        set_parts.append(f'updated_at = {ph}')
        params.append(datetime.now())
        # WHERE id = {ph} ... {perm_sql}：id 在前、perm 参数在后
        params.append(id)
        params.extend(perm_params)
        sql = (f'UPDATE {self._table} SET {", ".join(set_parts)} '
               f'WHERE id = {ph} AND is_deleted = 0 AND {perm_sql}')
        affected = rdb_mgr.execute(sql, tuple(params), name=self._db_name)
        return affected > 0

    def forget(self, id: int, shared_mode: bool = False) -> bool:
        """软删除（is_deleted=1），权限规则同 update。返回是否命中。"""
        ph = self._ph()
        perm_sql, perm_params = permission_clause(shared_mode, self._owner, ph)
        params: list[Any] = [datetime.now(), id]
        params.extend(perm_params)
        sql = (f'UPDATE {self._table} SET is_deleted = 1, updated_at = {ph} '
               f'WHERE id = {ph} AND is_deleted = 0 AND {perm_sql}')
        affected = rdb_mgr.execute(sql, tuple(params), name=self._db_name)
        return affected > 0

    def touch(self, id: int) -> None:
        """命中计数 +1、刷新 last_used_at（recall 命中副作用，不做鉴权）。"""
        ph = self._ph()
        sql = (f'UPDATE {self._table} SET use_count = use_count + 1, last_used_at = {ph} '
               f'WHERE id = {ph}')
        rdb_mgr.execute(sql, (datetime.now(), id), name=self._db_name)

    # endregion

    # region ======== 读操作 ========

    def get(self, id: int, shared_mode: bool = False) -> MemoryRecord | None:
        ph = self._ph()
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, ph)
        params: list[Any] = [id]
        params.extend(vis_params)
        sql = (f'SELECT * FROM {self._table} '
               f'WHERE id = {ph} AND is_deleted = 0 AND {vis_sql}')
        rows = rdb_mgr.query(sql, tuple(params), name=self._db_name)
        return MemoryRecord.from_dict(rows[0]) if rows else None

    def find_by_scope_title(
        self,
        scope: str,
        title: str,
        include_deleted: bool = False,
        shared_mode: bool = False,
        machine_bound: bool = False,
    ) -> list[MemoryRecord]:
        ph = self._ph()
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, ph)
        params: list[Any] = [scope, title]
        params.extend(vis_params)
        where = f'scope = {ph} AND title = {ph} AND {vis_sql}'
        # machine_bound 且当前有 machine 绑定时，追加本机隔离条件（self._machine 为空则退化忽略）
        if machine_bound and self._machine:
            where += f' AND machine = {ph}'
            params.append(self._machine)
        if not include_deleted:
            where += ' AND is_deleted = 0'
        sql = f'SELECT * FROM {self._table} WHERE {where}'
        rows = rdb_mgr.query(sql, tuple(params), name=self._db_name)
        return [MemoryRecord.from_dict(r) for r in rows]

    def count(self, include_deleted: bool = False, shared_mode: bool = False) -> int:
        ph = self._ph()
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, ph)
        params: list[Any] = list(vis_params)
        clauses = [vis_sql]
        if not include_deleted:
            clauses.append('is_deleted = 0')
        where = ' AND '.join(clauses)
        sql = f'SELECT COUNT(*) AS cnt FROM {self._table} WHERE {where}'
        rows = rdb_mgr.query(sql, tuple(params), name=self._db_name)
        return int(rows[0]['cnt']) if rows else 0

    def recall(
        self,
        query: str,
        scope: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_deleted: bool = False,
        shared_mode: bool = False,
    ) -> list[dict[str, Any]]:
        """
        模糊检索（参数化，按角色过滤可见范围）。

        SQL 结构：多 token × (title/keywords/content) 的 CASE WHEN 加权求和为
        ``_score``；WHERE 为各 token 的多字段 OR 组（无 token 时不过滤文本），
        叠加 is_deleted / 可见性 / scope / category 过滤；按 pinned→score→last_used_at 排序。
        """
        ph = self._ph()
        tokens = tokenize_query(query)
        params: list[Any] = []

        # 相关度分（SELECT 子句的占位符最先绑定）
        if tokens:
            score_terms: list[str] = []
            score_params: list[str] = []
            for tk in tokens:
                score_terms.append(
                    f'(CASE WHEN title LIKE {ph} THEN 3 ELSE 0 END'
                    f' + CASE WHEN keywords LIKE {ph} THEN 2 ELSE 0 END'
                    f' + CASE WHEN content LIKE {ph} THEN 1 ELSE 0 END)'
                )
                like = f'%{tk}%'
                score_params.extend([like, like, like])
            params.extend(score_params)
            score_expr = '(' + ' + '.join(score_terms) + ')'
        else:
            score_expr = '0'

        # WHERE 子句
        where_parts: list[str] = []
        if tokens:
            or_groups: list[str] = []
            for tk in tokens:
                or_groups.append(f'(title LIKE {ph} OR keywords LIKE {ph} OR content LIKE {ph})')
                like = f'%{tk}%'
                params.extend([like, like, like])
            where_parts.append('(' + ' OR '.join(or_groups) + ')')
        if not include_deleted:
            where_parts.append('is_deleted = 0')
        # 可见性（按角色）
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, ph)
        where_parts.append(vis_sql)
        params.extend(vis_params)
        if scope is not None:
            where_parts.append(f'scope = {ph}')
            params.append(scope)
        if category is not None:
            where_parts.append(f'category = {ph}')
            params.append(category)
        where_clause = ' AND '.join(where_parts)

        sql = (f'SELECT *, {score_expr} AS _score FROM {self._table}'
               f' WHERE {where_clause}'
               f' ORDER BY pinned DESC, _score DESC, last_used_at DESC'
               f' LIMIT {ph}')
        params.append(limit)
        return rdb_mgr.query(sql, tuple(params), name=self._db_name)

    # endregion


#: 模块级默认管理器实例：注册一个指向 rdb 默认实例的 RdbMemoryStore
memory_mgr: MemoryManager = MemoryManager()
memory_mgr.register(MemoryManager.DEFAULT_NAME, RdbMemoryStore())

__all__ = ['RdbMemoryStore', 'memory_mgr']

"""
计划任务服务的 MySQL 实现（基于 baibao 的 rdb_mgr）。

实现 :class:`pykunlun.task.plan.PlanTaskService` 抽象，核心要点：

  - **仅支持 MySQL**（本期唯一方言）：首次使用时探测目标实例的 ``db_type``，
    非 ``mysql`` 直接抛 ``NotImplementedError``；占位符固定 ``%s``；
  - **幂等初始化**——:meth:`MySqlPlanTaskService.setup` 用 ``CREATE TABLE IF NOT
    EXISTS`` 建 ``ai_task_*`` 五张表（列定义见 :mod:`.schema` 单一信息源）；
    首版无老库，不做补列迁移；
  - **事务化复合操作**——claim_next_step / finish_run / fail_run / sweep / cancel /
    add_steps 等经 ``rdb_mgr.get_connection()`` 裸连接 + 显式 ``commit/rollback``
    在同一事务内完成；单语句走 ``rdb_mgr.query/execute``；
  - **INSERT 回填 id**——走裸连接读 ``cursor.lastrowid``；
  - **JSON 列**（params）以 ``ensure_ascii=False``
    序列化写入，读取侧容错反序列化；时间统一 ``datetime.now()`` 落 DATETIME。
"""

import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, cast

from pykunlun.task.plan import (
    UPDATABLE_TASK_FIELDS,
    VALID_ART_TYPES,
    VALID_EVENT_LEVELS,
    VALID_EVENT_TYPES,
    VALID_STEP_TYPES,
    PlanTaskManager,
    PlanTaskService,
    PlanTask,
    TaskRun,
    TaskStep,
    deps_satisfied,
    parse_depends_on,
    step_disposition_on_fail,
)
from pykunlun.util import logutil

from baibao.db.rdb import rdb_mgr

from .schema import TABLES, ddl, sql_str

log = logutil.getLogger(__name__)


def _jdump(obj: Any) -> str | None:
    """对象 → JSON 字符串（ensure_ascii=False；None 直通），供 JSON 列落库。

    ``default=str``：input_snapshot 存续跑上下文包原文，其中 task/step 行含
    DATETIME 列还原出的 datetime 对象，自动转字符串（其余字段均为可序列化类型）。
    """
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, default=str)


def _jload(text: Any) -> Any:
    """JSON 字符串 → 对象，容错：非字符串/解析失败返回 None（列值可能被人工改过）。"""
    if text is None or text == '':
        return None
    if isinstance(text, (dict, list)):
        return cast(Any, text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        log.warning("JSON 列解析失败，按 None 处理: %.100s", text)
        return None


class MySqlPlanTaskService(PlanTaskService):
    """
    基于 rdb_mgr 的计划任务服务实现（仅 MySQL）。

    通过指定 rdb 实例名（``db_name``）复用 baibao 已注册的数据库连接；省略时使用
    rdb 的默认实例（``default``）。建议为计划任务单独注册一个别名实例（如
    ``agent_task``），与业务库、记忆库隔离。

    Args:
        db_name: rdb 实例名；None 用 rdb 默认实例。
        table_prefix: 表名前缀（默认空）。表名固定 ``ai_task_*``，前缀用于同一库内
            隔离多套任务表。
    """

    service_type = 'mysql'

    def __init__(self, db_name: str | None = None, table_prefix: str = '') -> None:
        self._db_name = db_name
        self._prefix = table_prefix or ''
        self._db_type: str | None = None  # 懒加载并缓存

    @property
    def db_name(self) -> str | None:
        return self._db_name

    def _t(self, base: str) -> str:
        """基名 → 实际表名（拼接前缀）。"""
        return f'{self._prefix}{base}'

    # region ======== 方言守卫与连接 ========
    def _get_db_type(self) -> str:
        """探测并缓存目标实例的数据库类型标识。"""
        if self._db_type is None:
            self._db_type = rdb_mgr.get_client(self._db_name).db_type
        return self._db_type

    def _require_mysql(self) -> None:
        """方言守卫：本期计划任务仅支持 MySQL，其余方言直接拒绝。"""
        db_type = self._get_db_type()
        if db_type != 'mysql':
            raise NotImplementedError(f"本期计划任务仅支持 MySQL，目标实例为 {db_type}")

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """单语句读（rdb_mgr.query，参数化 %s）。"""
        self._require_mysql()
        return rdb_mgr.query(sql, tuple(params), name=self._db_name)

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """单语句写（rdb_mgr.execute，参数化 %s），返回受影响行数。"""
        self._require_mysql()
        return rdb_mgr.execute(sql, tuple(params), name=self._db_name)

    @contextmanager
    def _tx(self) -> Generator[tuple[Any, Any], None, None]:
        """
        事务：从 rdb 实例借裸连接并建游标，正常退出 commit，异常回滚后重抛。

        池化连接的 ``close()`` 是归还连接池而非真关闭，故复合操作内多次执行共享
        同一事务。yield ``(conn, cur)``。
        """
        self._require_mysql()
        conn = rdb_mgr.get_connection(self._db_name)
        cur = None
        try:
            cur = conn.cursor()
            yield conn, cur
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                log.warning("事务回滚失败", exc_info=True)
            raise
        finally:
            if cur:
                cur.close()
            conn.close()

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        """裸游标结果 → 行字典列表（依 ``cur.description``，驱动无关）。"""
        if cur.description is None:
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @staticmethod
    def _lastrowid(cur: Any) -> int:
        """取 INSERT 回填的主键；lastrowid 为 None 属非预期，直接抛错。"""
        if cur.lastrowid is None:
            raise RuntimeError("INSERT 未返回 lastrowid（非预期，请检查表结构）")
        return int(cur.lastrowid)

    def _event(
        self,
        cur: Any,
        task_id: int,
        event_type: str,
        message: str,
        level: str = 'info',
        step_id: int | None = None,
        run_id: int | None = None,
    ) -> None:
        """事务内追加事件（append-only 留痕）。"""
        cur.execute(
            f'INSERT INTO {self._t("ai_task_event")} '
            f'(task_id, step_id, run_id, event_type, level, message, created_at) '
            f'VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (task_id, step_id, run_id, event_type, level, message, datetime.now()),
        )

    def _execute_event(self, task_id: int, event_type: str, message: str) -> None:
        """独立事务追加事件（供单语句状态流转后留痕）。"""
        with self._tx() as (_conn, cur):
            self._event(cur, task_id, event_type, message)
    # endregion

    # region ======== 行转换 ========
    def _task_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row['params'] = _jload(row.get('params'))
        # 物理列名沿用 template_id（表结构不动），领域字段名为 source_id（来源标识）
        row['source_id'] = row.pop('template_id', None)
        return row

    def _fetch_task(self, cur: Any, task_id: int) -> dict[str, Any] | None:
        cur.execute(f'SELECT * FROM {self._t("ai_task_instance")} WHERE id = %s', (task_id,))
        rows = self._rows(cur)
        return self._task_row(rows[0]) if rows else None

    def _touch_heartbeat(self, cur: Any, task_id: int) -> None:
        """刷新任务心跳（活动即心跳）。"""
        cur.execute(
            f'UPDATE {self._t("ai_task_instance")} '
            f'SET heartbeat_at = %s, updated_at = %s WHERE id = %s',
            (datetime.now(), datetime.now(), task_id))
    # endregion

    # region ======== 初始化 ========
    def setup(self) -> None:
        """幂等建 ``ai_task_*`` 五张表（utf8mb4 + 列/表注释内联），并给老库自动补新增列。"""
        for base in TABLES:
            self._execute(ddl(base, self._t(base)))
        self._migrate()
        log.info("MySqlPlanTaskService 已初始化 5 张 ai_task_* 表 (db_name=%s)", self._db_name)

    def _migrate(self) -> None:
        """轻量补列迁移：CREATE TABLE IF NOT EXISTS 不会给已存在的表补新列，
        此处按 information_schema 探测缺列并 ALTER（0.0.8 起的机制，新增列在此登记）。"""
        for base, (cols, _keys, _comment) in TABLES.items():
            table = self._t(base)
            rows = self._query(
                'SELECT COLUMN_NAME AS cn FROM information_schema.COLUMNS '
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
            existing = {r['cn'] for r in rows}
            if not existing:
                continue  # 表不存在（首次建表已含全部列）
            for col, col_def, note in cols:
                if col not in existing and 'PRIMARY KEY' not in col_def.upper():
                    self._execute(
                        f'ALTER TABLE {table} ADD COLUMN {col} {col_def} '
                        f'COMMENT {sql_str(note)}')
                    log.info("已补列 %s.%s", table, col)
    # endregion

    # region ======== 任务 ========
    def create_task(self, task: PlanTask) -> int:
        now = datetime.now()
        with self._tx() as (_conn, cur):
            cur.execute(
                f'INSERT INTO {self._t("ai_task_instance")} '
                f'(template_id, parent_task_id, title, goal, status, params, max_retries, '
                f'heartbeat_timeout_sec, timeout_sec, created_by, created_at, updated_at) '
                f'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (task.source_id, task.parent_task_id, task.title, task.goal, 'pending',
                 _jdump(task.params), task.max_retries, task.heartbeat_timeout_sec,
                 task.timeout_sec, task.created_by, now, now))
            task_id = self._lastrowid(cur)
            self._event(cur, task_id, 'note', f'任务创建: {task.title}')
        task.id = task_id
        return task_id

    def get_task(self, id: int) -> PlanTask | None:
        rows = self._query(f'SELECT * FROM {self._t("ai_task_instance")} WHERE id = %s', (id,))
        return PlanTask.from_dict(self._task_row(rows[0])) if rows else None

    def list_tasks(
        self,
        status: str | None = None,
        created_by: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append('t.status = %s')
            params.append(status)
        if created_by is not None:
            where.append('t.created_by = %s')
            params.append(created_by)
        where_clause = f'WHERE {" AND ".join(where)}' if where else ''
        s = self._t('ai_task_step')
        sql = (f'SELECT t.*, '
               f'(SELECT COUNT(*) FROM {s} WHERE {s}.task_id = t.id) AS total, '
               f'(SELECT COUNT(*) FROM {s} WHERE {s}.task_id = t.id AND {s}.status = %s) AS done '
               f'FROM {self._t("ai_task_instance")} t {where_clause} '
               f'ORDER BY t.id DESC LIMIT %s')
        # 子查询的 done 状态参数在最前，其后是 WHERE 参数，最后 LIMIT
        all_params = tuple(['succeeded'] + params + [limit])
        return [self._task_row(r) for r in self._query(sql, all_params)]

    def update_task(self, id: int, fields: dict[str, Any]) -> bool:
        allowed = {k: v for k, v in fields.items() if k in UPDATABLE_TASK_FIELDS}
        if not allowed:
            return False
        allowed = {k: _jdump(v) if k == 'params' else v for k, v in allowed.items()}
        set_parts = [f'{k} = %s' for k in allowed]
        params: list[Any] = list(allowed.values())
        set_parts.append('updated_at = %s')
        params.append(datetime.now())
        params.append(id)
        sql = f'UPDATE {self._t("ai_task_instance")} SET {", ".join(set_parts)} WHERE id = %s'
        return self._execute(sql, tuple(params)) > 0

    def heartbeat(self, id: int) -> None:
        self._execute(
            f'UPDATE {self._t("ai_task_instance")} '
            f'SET heartbeat_at = %s, updated_at = %s WHERE id = %s',
            (datetime.now(), datetime.now(), id))

    def pause(self, id: int) -> bool:
        now = datetime.now()
        affected = self._execute(
            f'UPDATE {self._t("ai_task_instance")} '
            f'SET status = %s, heartbeat_at = %s, updated_at = %s '
            f'WHERE id = %s AND status = %s',
            ('paused', now, now, id, 'running'))
        if affected:
            self._execute_event(id, 'state_change', 'task: running → paused')
        return affected > 0

    def resume(self, id: int) -> bool:
        now = datetime.now()
        affected = self._execute(
            f'UPDATE {self._t("ai_task_instance")} '
            f'SET status = %s, heartbeat_at = %s, updated_at = %s '
            f'WHERE id = %s AND status = %s',
            ('running', now, now, id, 'paused'))
        if affected:
            self._execute_event(id, 'state_change', 'task: paused → running')
        return affected > 0

    def cancel(self, id: int, reason: str = '') -> bool:
        now = datetime.now()
        with self._tx() as (_conn, cur):
            task = self._fetch_task(cur, id)
            if task is None:
                log.warning("cancel 未找到任务 id=%s", id)
                return False
            if task['status'] in ('completed', 'failed', 'cancelled'):
                log.info("任务 id=%s 已终态(%s)，无需取消", id, task['status'])
                return False
            # 连带处理：running 步骤置 failed（注明取消）、running run 置 cancelled
            cur.execute(
                f'SELECT * FROM {self._t("ai_task_step")} '
                f'WHERE task_id = %s AND status = %s', (id, 'running'))
            steps = self._rows(cur)
            cur.execute(
                f'UPDATE {self._t("ai_task_run")} '
                f'SET status = %s, finished_at = %s WHERE task_id = %s AND status = %s',
                ('cancelled', now, id, 'running'))
            for s in steps:
                cur.execute(
                    f'UPDATE {self._t("ai_task_step")} '
                    f'SET status = %s, finished_at = %s, updated_at = %s '
                    f'WHERE id = %s AND status = %s',
                    ('failed', now, now, s['id'], 'running'))
                self._event(cur, id, 'state_change',
                            f"step {s['id']}: running → failed (cancelled)", step_id=s['id'])
            cur.execute(
                f'UPDATE {self._t("ai_task_instance")} '
                f'SET status = %s, finished_at = %s, updated_at = %s WHERE id = %s',
                ('cancelled', now, now, id))
            msg = 'task: → cancelled' + (f' ({reason})' if reason else '')
            self._event(cur, id, 'state_change', msg)
        return True
    # endregion

    # region ======== 步骤 ========
    def _validate_step(self, step: TaskStep) -> None:
        if not step.name or not step.name.strip():
            raise ValueError("步骤 name 不能为空")
        if not step.instruction or not step.instruction.strip():
            raise ValueError(f"步骤 {step.name!r} 的 instruction 不能为空")
        if step.step_type not in VALID_STEP_TYPES:
            raise ValueError(
                f"非法的 step_type: {step.step_type!r}（合法值: {sorted(VALID_STEP_TYPES)}）")
        if step.depends_on is not None:
            deps = step.depends_on
            if not isinstance(deps, list) or any(
                    isinstance(v, bool) or not isinstance(v, int) for v in deps):
                raise ValueError(
                    f"步骤 {step.name!r} 的 depends_on 须为 int 列表（依赖步骤的 seq）")
            if len(set(deps)) != len(deps):
                raise ValueError(f"步骤 {step.name!r} 的 depends_on 含重复 seq")

    def _check_deps_vs_seq(self, step: TaskStep, seq: int) -> None:
        """seq 取号后校验依赖方向：只能依赖同任务更早的 seq（防环/防自依赖）。"""
        if not step.depends_on:
            return
        bad = [d for d in step.depends_on if d <= 0 or d >= seq]
        if bad:
            raise ValueError(
                f"步骤 {step.name!r} 的 depends_on 只能引用同任务更早的 seq "
                f"（非法: {bad}，自身 seq={seq}）")

    def _resolve_step_defaults(self, cur: Any, step: TaskStep) -> tuple[int, int]:
        """补全 seq（缺省 max+1）与 max_retries（缺省继承任务），返回 (seq, max_retries)。"""
        cur.execute(f'SELECT MAX(seq) AS mx FROM {self._t("ai_task_step")} '
                    f'WHERE task_id = %s', (step.task_id,))
        rows = self._rows(cur)
        base = int(rows[0]['mx']) if rows and rows[0]['mx'] is not None else 0
        seq = step.seq if step.seq is not None else base + 1
        if seq <= base:
            raise ValueError(f"步骤 seq={seq} 与已有步骤冲突（当前最大 seq={base}）")
        if step.max_retries is not None:
            mr = step.max_retries
        else:
            task = self._fetch_task(cur, step.task_id)
            if task is None:
                raise ValueError(f"任务不存在: id={step.task_id}")
            mr = int(task['max_retries'])
        return seq, mr

    def _insert_step(self, cur: Any, step: TaskStep, seq: int, max_retries: int) -> int:
        now = datetime.now()
        cur.execute(
            f'INSERT INTO {self._t("ai_task_step")} '
            f'(task_id, seq, name, step_type, instruction, status, retry_count, max_retries, '
            f'timeout_sec, depends_on, created_at, updated_at) '
            f'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (step.task_id, seq, step.name, step.step_type, step.instruction, 'pending',
             step.retry_count, max_retries, step.timeout_sec, _jdump(step.depends_on),
             now, now))
        return self._lastrowid(cur)

    def add_step(self, step: TaskStep) -> int:
        self._validate_step(step)
        with self._tx() as (_conn, cur):
            seq, mr = self._resolve_step_defaults(cur, step)
            self._check_deps_vs_seq(step, seq)
            step_id = self._insert_step(cur, step, seq, mr)
            self._event(cur, step.task_id, 'note',
                        f'step added: seq={seq} {step.name} (step_id={step_id})')
        step.seq = seq
        step.max_retries = mr
        step.id = step_id
        return step_id

    def add_steps(self, steps: list[TaskStep]) -> int:
        if not steps:
            raise ValueError("steps 不能为空")
        task_ids = {s.task_id for s in steps}
        if len(task_ids) > 1:
            raise ValueError(f"批量导入的步骤须属于同一任务（发现 {sorted(task_ids)}）")
        for s in steps:
            self._validate_step(s)
        with self._tx() as (_conn, cur):
            # 先整体校验（任务存在性 + seq 取号），全部通过才插入
            resolved: list[tuple[TaskStep, int, int]] = []
            base = 0
            for s in steps:
                seq = s.seq if s.seq is not None else base + 1
                if seq <= base:
                    raise ValueError(f"步骤 {s.name!r} 的 seq={seq} 与前序冲突（当前最大 {base}）")
                base = seq
                self._check_deps_vs_seq(s, seq)
                mr = s.max_retries
                if mr is None:
                    task = self._fetch_task(cur, s.task_id)
                    if task is None:
                        raise ValueError(f"任务不存在: id={s.task_id}")
                    mr = int(task['max_retries'])
                resolved.append((s, seq, mr))
            for s, seq, mr in resolved:
                sid = self._insert_step(cur, s, seq, mr)
                s.id, s.seq, s.max_retries = sid, seq, mr
            self._event(cur, steps[0].task_id, 'note', f'批量导入 {len(steps)} 个步骤')
        return len(resolved)

    def get_step(self, id: int) -> TaskStep | None:
        rows = self._query(f'SELECT * FROM {self._t("ai_task_step")} WHERE id = %s', (id,))
        return TaskStep.from_dict(rows[0]) if rows else None

    def list_steps(self, task_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_step")} WHERE task_id = %s ORDER BY seq', (task_id,))

    def skip_step(self, id: int, reason: str = '') -> bool:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        now = datetime.now()
        with self._tx() as (_conn, cur):
            cur.execute(f'SELECT * FROM {t_step} WHERE id = %s', (id,))
            rows = self._rows(cur)
            if not rows:
                log.warning("skip 未找到步骤 id=%s", id)
                return False
            cur.execute(
                f'UPDATE {t_step} '
                f'SET status = %s, finished_at = %s, updated_at = %s '
                f'WHERE id = %s AND status = %s',
                ('skipped', now, now, id, 'pending'))
            if not cur.rowcount:
                log.warning("步骤 id=%s 非 pending（%s），不可 skip", id, rows[0]['status'])
                return False
            msg = f"step {id}: pending → skipped" + (f' ({reason})' if reason else '')
            self._event(cur, rows[0]['task_id'], 'state_change', msg, step_id=id)
            # 收口判定与 finish_run 一致：skip 视同有意完成，跳过最后剩余步骤时
            # 任务应收口（仅 running 任务；pending 任务跳完全部步骤保持 pending）。
            cur.execute(
                f'SELECT status, COUNT(*) AS n FROM {t_step} '
                f'WHERE task_id = %s AND status IN (%s,%s,%s) GROUP BY status',
                (rows[0]['task_id'], 'pending', 'running', 'failed'))
            counts = {r['status']: r['n'] for r in self._rows(cur)}
            if not counts.get('pending') and not counts.get('running') and not counts.get('failed'):
                cur.execute(
                    f'UPDATE {t_task} SET status = %s, finished_at = %s, heartbeat_at = %s, '
                    f'updated_at = %s WHERE id = %s AND status = %s',
                    ('completed', now, now, now, rows[0]['task_id'], 'running'))
                self._event(cur, rows[0]['task_id'], 'state_change',
                            'task: running → completed (剩余步骤全部跳过)')
        return True

    def retry_step(self, id: int, force: bool = False) -> bool:
        now = datetime.now()
        with self._tx() as (_conn, cur):
            cur.execute(f'SELECT * FROM {self._t("ai_task_step")} WHERE id = %s', (id,))
            rows = self._rows(cur)
            if not rows:
                log.warning("retry 未找到步骤 id=%s", id)
                return False
            step = rows[0]
            allowed = ('failed', 'skipped', 'succeeded') if force else ('failed', 'skipped')
            if step['status'] not in allowed:
                log.warning("步骤 id=%s 状态为 %s，仅 failed/skipped 可 retry%s",
                            id, step['status'], "（force 可加 succeeded）" if not force else "")
                return False
            clear_summary = force and step['status'] == 'succeeded'
            sets = ('status = %s, max_retries = max_retries + 1, updated_at = %s'
                    if not clear_summary else
                    'status = %s, max_retries = max_retries + 1, result_summary = NULL, '
                    'finished_at = NULL, updated_at = %s')
            cur.execute(
                f'UPDATE {self._t("ai_task_step")} SET {sets} WHERE id = %s',
                ('pending', now, id))
            tag = 'manual retry, budget+1' + (', force' if force else '')
            self._event(cur, step['task_id'], 'state_change',
                        f'step {id}: {step["status"]} → pending ({tag})', step_id=id)
            # 任务已终态且可归因于该步骤时一并复活（failed 常规；completed 仅 force——
            # 正常收口不会 failed，被改库伪造的 completed 需随假成功修复一起回 running）
            task = self._fetch_task(cur, step['task_id'])
            revive_from = ('failed', 'completed') if force else ('failed',)
            if task is not None and task['status'] in revive_from:
                cur.execute(
                    f'UPDATE {self._t("ai_task_instance")} '
                    f'SET status = %s, finished_at = NULL, heartbeat_at = %s, updated_at = %s '
                    f'WHERE id = %s AND status = %s',
                    ('running', now, now, task['id'], task['status']))
                self._event(cur, task['id'], 'state_change',
                            f'task: {task["status"]} → running (manual retry revive)')
        return True
    # endregion

    # region ======== 执行 ========
    def claim_next_step(
        self,
        task_id: int,
        session_id: str | None = None,
        agent_name: str | None = None,
        ignore_deps: bool = False,
    ) -> dict[str, Any] | None:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        now = datetime.now()
        with self._tx() as (_conn, cur):
            task = self._fetch_task(cur, task_id)
            if task is None:
                raise ValueError(f"任务不存在: id={task_id}")
            if task['status'] not in ('pending', 'running'):
                log.info("任务 id=%s 状态为 %s，无步骤可认领", task_id, task['status'])
                return None
            # 乐观锁循环：条件 UPDATE 抢占失败（被并发抢走/状态已变）则重选候选
            for _ in range(3):
                cur.execute(
                    f'SELECT * FROM {t_step} WHERE task_id = %s AND status = %s '
                    f'ORDER BY seq', (task_id, 'pending'))
                cands = self._rows(cur)
                if not cands:
                    return None
                # 依赖感知候选选取：任务内任一步骤声明了 depends_on 时启用，
                # 按 seq 升序取第一个依赖就绪的 pending；无声明保持旧行为（seq 最小）
                cand = None
                if not ignore_deps:
                    cur.execute(
                        f'SELECT seq, status, depends_on FROM {t_step} '
                        f'WHERE task_id = %s', (task_id,))
                    all_steps = self._rows(cur)
                    if any(parse_depends_on(s['depends_on']) for s in all_steps):
                        status_by_seq = {s['seq']: s['status'] for s in all_steps}
                        cand = next(
                            (c for c in cands
                             if deps_satisfied(parse_depends_on(c['depends_on']),
                                               status_by_seq)), None)
                        if cand is None:
                            log.info("任务 id=%s 的 pending 步骤依赖均未就绪，暂无可认领",
                                     task_id)
                            return None
                if cand is None:
                    cand = cands[0]
                cur.execute(
                    f'UPDATE {t_step} SET status = %s, started_at = COALESCE(started_at, %s), '
                    f'updated_at = %s WHERE id = %s AND status = %s',
                    ('running', now, now, cand['id'], 'pending'))
                if not cur.rowcount:
                    continue
                # 任务首次 claim：pending → running；否则仅刷心跳
                if task['status'] == 'pending':
                    cur.execute(
                        f'UPDATE {t_task} SET status = %s, started_at = COALESCE(started_at, %s), '
                        f'heartbeat_at = %s, updated_at = %s WHERE id = %s AND status = %s',
                        ('running', now, now, now, task_id, 'pending'))
                    self._event(cur, task_id, 'state_change',
                                'task: pending → running (first claim)')
                else:
                    self._touch_heartbeat(cur, task_id)
                # 续跑上下文包：任务 + 步骤 + run_id + 前序成功步骤摘要
                cur.execute(
                    f'SELECT seq, name, result_summary FROM {t_step} '
                    f'WHERE task_id = %s AND status = %s ORDER BY seq',
                    (task_id, 'succeeded'))
                ctx = self._rows(cur)
                cur.execute(f'SELECT * FROM {t_task} WHERE id = %s', (task_id,))
                trow = self._task_row(self._rows(cur)[0])
                cur.execute(f'SELECT * FROM {t_step} WHERE id = %s', (cand['id'],))
                srow = self._rows(cur)[0]
                package: dict[str, Any] = {'task': trow, 'step': dict(srow), 'context': ctx}
                cur.execute(
                    f'INSERT INTO {self._t("ai_task_run")} '
                    f'(task_id, step_id, session_id, agent_name, status, input_snapshot, '
                    f'started_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (task_id, cand['id'], session_id, agent_name, 'running',
                     _jdump(package), now))
                run_id = self._lastrowid(cur)
                self._event(
                    cur, task_id, 'state_change',
                    f"step {cand['id']}: pending → running "
                    f"(claim by {session_id or 'anonymous'})",
                    step_id=cand['id'], run_id=run_id)
                package['run_id'] = run_id
                return package
        log.warning("claim 连续 3 次抢占失败（task=%s），放弃本次认领", task_id)
        return None

    def finish_run(self, run_id: int, output: str = '', summary: str | None = None,
                   token_usage: int | None = None) -> bool:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        now = datetime.now()
        with self._tx() as (_conn, cur):
            cur.execute(f'SELECT * FROM {t_run} WHERE id = %s', (run_id,))
            runs = self._rows(cur)
            if not runs:
                log.warning("finish 未找到 run id=%s", run_id)
                return False
            run = runs[0]
            if run['status'] != 'running':
                log.warning("run id=%s 已终态(%s)，忽略 finish", run_id, run['status'])
                return False
            if summary is None:
                summary = output[:2000] if output else ''
            cur.execute(
                f'UPDATE {t_run} SET status = %s, output = %s, token_usage = %s, '
                f'finished_at = %s WHERE id = %s AND status = %s',
                ('succeeded', output, token_usage, now, run_id, 'running'))
            cur.execute(
                f'UPDATE {t_step} SET status = %s, result_summary = %s, finished_at = %s, '
                f'updated_at = %s WHERE id = %s AND status = %s',
                ('succeeded', summary, now, now, run['step_id'], 'running'))
            self._event(cur, run['task_id'], 'state_change',
                        f'run {run_id}: running → succeeded', step_id=run['step_id'],
                        run_id=run_id)
            self._event(cur, run['task_id'], 'state_change',
                        f"step {run['step_id']}: running → succeeded", step_id=run['step_id'])
            # 收口判定：无 pending/running 且无 failed → 任务完成
            cur.execute(
                f'SELECT status, COUNT(*) AS n FROM {t_step} '
                f'WHERE task_id = %s AND status IN (%s,%s,%s) GROUP BY status',
                (run['task_id'], 'pending', 'running', 'failed'))
            counts = {r['status']: r['n'] for r in self._rows(cur)}
            if not counts.get('pending') and not counts.get('running') and not counts.get('failed'):
                cur.execute(
                    f'UPDATE {t_task} SET status = %s, finished_at = %s, heartbeat_at = %s, '
                    f'updated_at = %s WHERE id = %s AND status = %s',
                    ('completed', now, now, now, run['task_id'], 'running'))
                self._event(cur, run['task_id'], 'state_change', 'task: running → completed')
            else:
                self._touch_heartbeat(cur, run['task_id'])
        return True

    def fail_run(self, run_id: int, error: str) -> str:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        now = datetime.now()
        with self._tx() as (_conn, cur):
            cur.execute(f'SELECT * FROM {t_run} WHERE id = %s', (run_id,))
            runs = self._rows(cur)
            if not runs:
                log.warning("fail 未找到 run id=%s", run_id)
                return ''
            run = runs[0]
            if run['status'] != 'running':
                log.warning("run id=%s 已终态(%s)，忽略 fail", run_id, run['status'])
                return ''
            cur.execute(
                f'UPDATE {t_run} SET status = %s, error_msg = %s, finished_at = %s WHERE id = %s',
                ('failed', error, now, run_id))
            self._event(cur, run['task_id'], 'error',
                        f'run {run_id} 失败: {error[:500]}', level='warn',
                        step_id=run['step_id'], run_id=run_id)
            self._event(cur, run['task_id'], 'state_change',
                        f'run {run_id}: running → failed', step_id=run['step_id'],
                        run_id=run_id)
            cur.execute(f'SELECT * FROM {t_step} WHERE id = %s', (run['step_id'],))
            step = self._rows(cur)[0]
            mr = int(step['max_retries']) if step['max_retries'] is not None else 0
            disposition = step_disposition_on_fail(int(step['retry_count']), mr)
            if disposition == 'pending':
                # 预算未耗尽：步骤回 pending（retry_count+1），任务不变，刷心跳
                cur.execute(
                    f'UPDATE {t_step} SET status = %s, retry_count = retry_count + 1, '
                    f'updated_at = %s WHERE id = %s AND status = %s',
                    ('pending', now, step['id'], 'running'))
                self._event(cur, run['task_id'], 'state_change',
                            f"step {step['id']}: running → pending "
                            f"(retry {step['retry_count'] + 1}/{mr})", step_id=step['id'])
                self._touch_heartbeat(cur, run['task_id'])
            else:
                # 预算耗尽：步骤终败，任务连带失败
                cur.execute(
                    f'UPDATE {t_step} SET status = %s, finished_at = %s, updated_at = %s '
                    f'WHERE id = %s AND status = %s',
                    ('failed', now, now, step['id'], 'running'))
                self._event(cur, run['task_id'], 'state_change',
                            f"step {step['id']}: running → failed (预算耗尽)",
                            step_id=step['id'])
                cur.execute(
                    f'UPDATE {t_task} SET status = %s, finished_at = %s, updated_at = %s '
                    f'WHERE id = %s AND status = %s',
                    ('failed', now, now, run['task_id'], 'running'))
                self._event(cur, run['task_id'], 'state_change',
                            'task: running → failed (步骤预算耗尽)')
            return 'retried' if disposition == 'pending' else 'step_failed'

    def get_run(self, run_id: int) -> TaskRun | None:
        rows = self._query(f'SELECT * FROM {self._t("ai_task_run")} WHERE id = %s', (run_id,))
        return TaskRun.from_dict(rows[0]) if rows else None

    def list_runs(self, step_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_run")} WHERE step_id = %s ORDER BY id', (step_id,))

    def list_task_runs(self, task_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_run")} WHERE task_id = %s ORDER BY id', (task_id,))

    def release_run(self, run_id: int, reason: str = '') -> str:
        now = datetime.now()
        with self._tx() as (_conn, cur):
            cur.execute(f'SELECT * FROM {self._t("ai_task_run")} WHERE id = %s', (run_id,))
            runs = self._rows(cur)
            if not runs:
                log.warning("release 未找到 run id=%s", run_id)
                return ''
            run = runs[0]
            if run['status'] != 'running':
                log.warning("run id=%s 已终态(%s)，忽略 release", run_id, run['status'])
                return ''
            cur.execute(
                f'UPDATE {self._t("ai_task_run")} SET status = %s, error_msg = %s, '
                f'finished_at = %s WHERE id = %s AND status = %s',
                ('cancelled', 'released' + (f': {reason}' if reason else ''),
                 now, run_id, 'running'))
            self._event(cur, run['task_id'], 'state_change',
                        f'run {run_id}: running → cancelled '
                        f'(release{": " + reason if reason else ""})',
                        step_id=run['step_id'], run_id=run_id)
            # 步骤还回队列：不加 retry_count（释放 ≠ 失败，不消耗重试预算）
            cur.execute(
                f'UPDATE {self._t("ai_task_step")} SET status = %s, updated_at = %s '
                f'WHERE id = %s AND status = %s',
                ('pending', now, run['step_id'], 'running'))
            if cur.rowcount:
                self._event(cur, run['task_id'], 'state_change',
                            f"step {run['step_id']}: running → pending "
                            f"(released, 预算不变)", step_id=run['step_id'])
            self._touch_heartbeat(cur, run['task_id'])
        return 'released'

    # ---- 恢复（sweep）----

    def _reap_step(self, cur: Any, step: dict[str, Any], reason: str,
                   now: datetime) -> list[dict[str, Any]]:
        """僵尸步骤处理：其 running run 置 timeout，步骤按重试预算回 pending 或终败。

        返回被恢复对象的摘要列表（task 级与 step 级 sweep 共用）。
        """
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        out: list[dict[str, Any]] = []
        cur.execute(f'SELECT * FROM {t_run} WHERE step_id = %s AND status = %s',
                    (step['id'], 'running'))
        runs = self._rows(cur)
        for r in runs:
            cur.execute(
                f'UPDATE {t_run} SET status = %s, error_msg = %s, finished_at = %s '
                f'WHERE id = %s AND status = %s',
                ('timeout', reason, now, r['id'], 'running'))
            out.append({'task_id': r['task_id'], 'step_id': step['id'], 'run_id': r['id'],
                        'action': 'run_timeout', 'detail': reason})
            self._event(cur, r['task_id'], 'state_change',
                        f"run {r['id']}: running → timeout ({reason})",
                        step_id=step['id'], run_id=r['id'])
        mr = int(step['max_retries']) if step['max_retries'] is not None else 0
        disposition = step_disposition_on_fail(int(step['retry_count']), mr)
        if disposition == 'pending':
            cur.execute(
                f'UPDATE {t_step} SET status = %s, retry_count = retry_count + 1, updated_at = %s '
                f'WHERE id = %s AND status = %s',
                ('pending', now, step['id'], 'running'))
            out.append({'task_id': step['task_id'], 'step_id': step['id'], 'run_id': None,
                        'action': 'step_retry', 'detail': f'retry {step["retry_count"] + 1}/{mr}'})
            self._event(cur, step['task_id'], 'state_change',
                        f"step {step['id']}: running → pending (sweep retry)", step_id=step['id'])
        else:
            cur.execute(
                f'UPDATE {t_step} SET status = %s, finished_at = %s, updated_at = %s '
                f'WHERE id = %s AND status = %s',
                ('failed', now, now, step['id'], 'running'))
            out.append({'task_id': step['task_id'], 'step_id': step['id'], 'run_id': None,
                        'action': 'step_failed', 'detail': reason})
            self._event(cur, step['task_id'], 'state_change',
                        f"step {step['id']}: running → failed (sweep, 预算耗尽)",
                        step_id=step['id'])
            cur.execute(
                f'UPDATE {t_task} SET status = %s, finished_at = %s, updated_at = %s '
                f'WHERE id = %s AND status = %s',
                ('failed', now, now, step['task_id'], 'running'))
            self._event(cur, step['task_id'], 'state_change',
                        'task: running → failed (sweep, 步骤预算耗尽)')
        return out

    def _reap_task_timeout(self, cur: Any, task: dict[str, Any],
                           now: datetime) -> list[dict[str, Any]]:
        """任务总超时终态化：running run 置 timeout、running 步骤置 failed、任务置 failed。

        不走重试预算裁决——任务整体时间预算已尽，重试无意义；
        可用 retry_step 手动复活（预算 +1 且任务回 running）。
        """
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        out: list[dict[str, Any]] = []
        reason = 'task total timeout'
        cur.execute(f'SELECT * FROM {t_step} WHERE task_id = %s AND status = %s',
                    (task['id'], 'running'))
        steps = self._rows(cur)
        for s in steps:
            cur.execute(f'SELECT * FROM {t_run} WHERE step_id = %s AND status = %s',
                        (s['id'], 'running'))
            for r in self._rows(cur):
                cur.execute(
                    f'UPDATE {t_run} SET status = %s, error_msg = %s, finished_at = %s '
                    f'WHERE id = %s AND status = %s',
                    ('timeout', reason, now, r['id'], 'running'))
                out.append({'task_id': task['id'], 'step_id': s['id'], 'run_id': r['id'],
                            'action': 'task_timeout', 'detail': reason})
                self._event(cur, task['id'], 'state_change',
                            f"run {r['id']}: running → timeout ({reason})",
                            step_id=s['id'], run_id=r['id'])
            cur.execute(
                f'UPDATE {t_step} SET status = %s, finished_at = %s, updated_at = %s '
                f'WHERE id = %s AND status = %s',
                ('failed', now, now, s['id'], 'running'))
            out.append({'task_id': task['id'], 'step_id': s['id'], 'run_id': None,
                        'action': 'step_failed', 'detail': reason})
            self._event(cur, task['id'], 'state_change',
                        f"step {s['id']}: running → failed ({reason})", step_id=s['id'])
        cur.execute(
            f'UPDATE {t_task} SET status = %s, finished_at = %s, updated_at = %s '
            f'WHERE id = %s AND status = %s',
            ('failed', now, now, task['id'], 'running'))
        out.append({'task_id': task['id'], 'step_id': None, 'run_id': None,
                    'action': 'task_failed', 'detail': reason})
        self._event(cur, task['id'], 'state_change',
                    f"task {task['id']}: running → failed (任务总超时)")
        return out

    def sweep(self, heartbeat_timeout_sec: int | None = None) -> list[dict[str, Any]]:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        results: list[dict[str, Any]] = []
        now = datetime.now()
        with self._tx() as (_conn, cur):
            # ⓪ 任务级：总超时（timeout_sec 非空且 started_at 距今超过它）——任务连同
            # running 步骤直接终败，不走预算裁决。须先于心跳检测执行。
            cur.execute(
                f'SELECT * FROM {t_task} WHERE status = %s AND timeout_sec IS NOT NULL',
                ('running',))
            for t in self._rows(cur):
                if not t['started_at']:
                    continue
                if t['started_at'] >= now - timedelta(seconds=int(t['timeout_sec'])):
                    continue
                results.extend(self._reap_task_timeout(cur, t, now))
            # ① 任务级：心跳超时的 running 任务，其 running 步骤按僵尸处理。
            # 阈值比较放在 Python 侧（heartbeat_at 由驱动还原为 datetime），
            # 以兼容 heartbeat_timeout_sec 全局覆盖参数。
            cur.execute(f'SELECT * FROM {t_task} WHERE status = %s', ('running',))
            running_tasks = self._rows(cur)
            for t in running_tasks:
                if not t['heartbeat_at']:
                    continue
                timeout = (heartbeat_timeout_sec if heartbeat_timeout_sec is not None
                           else int(t['heartbeat_timeout_sec'])
                           if t['heartbeat_timeout_sec'] is not None else 1800)
                if t['heartbeat_at'] >= now - timedelta(seconds=timeout):
                    continue
                cur.execute(
                    f'SELECT * FROM {t_step} WHERE task_id = %s AND status = %s',
                    (t['id'], 'running'))
                zombie_steps = self._rows(cur)
                for s in zombie_steps:
                    results.extend(self._reap_step(cur, s, 'heartbeat timeout', now))
            # ② 步骤级：任务心跳正常，但 run.started_at 超过 step.timeout_sec（单步卡死）
            cur.execute(
                f'SELECT s.* FROM {t_step} s JOIN {t_run} r ON r.step_id = s.id '
                f'WHERE r.status = %s AND s.timeout_sec IS NOT NULL AND s.status = %s',
                ('running', 'running'))
            candidates = self._rows(cur)
            for s in candidates:
                threshold = now - timedelta(seconds=int(s['timeout_sec']))
                cur.execute(
                    f'SELECT * FROM {t_run} WHERE step_id = %s AND status = %s '
                    f'AND started_at IS NOT NULL AND started_at < %s',
                    (s['id'], 'running', threshold))
                if self._rows(cur):
                    results.extend(self._reap_step(cur, s, 'step timeout', now))
            if results:
                log.info("sweep 恢复了 %d 个对象", len(results))
        return results

    def verify_task(self, task_id: int, fix: bool = False) -> list[dict[str, Any]]:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        t_event = self._t('ai_task_event')
        now = datetime.now()
        findings: list[dict[str, Any]] = []
        with self._tx() as (_conn, cur):
            task = self._fetch_task(cur, task_id)
            if task is None:
                raise ValueError(f"任务不存在: id={task_id}")
            cur.execute(f'SELECT * FROM {t_step} WHERE task_id = %s ORDER BY seq', (task_id,))
            steps = self._rows(cur)
            cur.execute(f'SELECT * FROM {t_run} WHERE task_id = %s', (task_id,))
            runs = self._rows(cur)
            cur.execute(f'SELECT message FROM {t_event} WHERE task_id = %s', (task_id,))
            event_msgs = [r['message'] for r in self._rows(cur)]
            runs_by_step: dict[int, list[dict[str, Any]]] = {}
            for r in runs:
                runs_by_step.setdefault(r['step_id'], []).append(r)
            step_by_id = {s['id']: s for s in steps}

            def has_event(sid: int, fragment: str) -> bool:
                return any(m.startswith(f'step {sid}: ') and fragment in m
                           for m in event_msgs)

            def running_runs(sid: int) -> list[dict[str, Any]]:
                return [r for r in runs_by_step.get(sid, []) if r['status'] == 'running']

            for s in steps:
                sid = s['id']
                if s['status'] == 'succeeded':
                    # V1 假成功：真完成须同时有 succeeded run 与 finish 事件（SQL 直刷
                    # 不产生二者）。修复取向：有活 run 接管为 running，否则回 pending。
                    ok_run = any(r['status'] == 'succeeded' for r in runs_by_step.get(sid, []))
                    ok_event = has_event(sid, 'running → succeeded')
                    if ok_run and ok_event:
                        continue
                    detail = ('假成功（缺 succeeded run' +
                              ('' if ok_event and ok_run else
                               '与 finish 事件' if ok_run else '，缺 finish 事件' if ok_event
                               else '与 finish 事件') + '）')
                    finding = {'rule': 'V1', 'level': 'error', 'kind': 'step', 'id': sid,
                               'detail': detail, 'fixed': None}
                    findings.append(finding)
                    if fix:
                        live = running_runs(sid)
                        if live:
                            cur.execute(
                                f'UPDATE {t_step} SET status = %s, result_summary = NULL, '
                                f'updated_at = %s WHERE id = %s AND status = %s',
                                ('running', now, sid, 'succeeded'))
                            self._event(cur, task_id, 'state_change',
                                        f'step {sid}: succeeded → running '
                                        f'(verify fix, 接管活 run)', step_id=sid)
                        else:
                            cur.execute(
                                f'UPDATE {t_step} SET status = %s, result_summary = NULL, '
                                f'finished_at = NULL, updated_at = %s '
                                f'WHERE id = %s AND status = %s',
                                ('pending', now, sid, 'succeeded'))
                            self._event(cur, task_id, 'state_change',
                                        f'step {sid}: succeeded → pending '
                                        f'(verify fix, 清假摘要)', step_id=sid)
                        finding['fixed'] = True
                elif s['status'] == 'running':
                    # V2 僵尸步骤：状态在跑但没有任何 running run 支撑
                    if not running_runs(sid):
                        finding = {'rule': 'V2', 'level': 'error', 'kind': 'step', 'id': sid,
                                   'detail': 'running 但无任何 running run（无执行支撑）',
                                   'fixed': None}
                        findings.append(finding)
                        if fix:
                            cur.execute(
                                f'UPDATE {t_step} SET status = %s, updated_at = %s '
                                f'WHERE id = %s AND status = %s',
                                ('pending', now, sid, 'running'))
                            self._event(cur, task_id, 'state_change',
                                        f'step {sid}: running → pending '
                                        f'(verify fix, 清无支撑的 running)', step_id=sid)
                            finding['fixed'] = True
                elif s['status'] == 'skipped':
                    # V6 无 skip 事件的 skipped：来源不明（仅告警，不动状态）
                    if not has_event(sid, '→ skipped'):
                        findings.append({'rule': 'V6', 'level': 'info', 'kind': 'step',
                                         'id': sid, 'detail': 'skipped 但无 skip 事件（来源不明）',
                                         'fixed': None})
            # V1/V2 修复会改动步骤状态，V3/V4/V5 须基于**修复后**的最新状态判定——重查
            cur.execute(f'SELECT * FROM {t_step} WHERE task_id = %s ORDER BY seq', (task_id,))
            steps = self._rows(cur)
            step_by_id = {s['id']: s for s in steps}
            # V3 孤儿 run：仍在 running 但所属步骤已不在 running
            for r in runs:
                if r['status'] != 'running':
                    continue
                st = step_by_id.get(r['step_id'])
                if st is not None and st['status'] == 'running':
                    continue
                finding = {'rule': 'V3', 'level': 'warn', 'kind': 'run', 'id': r['id'],
                           'detail': f'run 仍在 running，但所属步骤状态为 '
                                     f'{st["status"] if st else "不存在"}',
                           'fixed': None}
                findings.append(finding)
                if fix:
                    cur.execute(
                        f'UPDATE {t_run} SET status = %s, error_msg = %s, finished_at = %s '
                        f'WHERE id = %s AND status = %s',
                        ('cancelled', 'verify: 孤儿 run', now, r['id'], 'running'))
                    self._event(cur, task_id, 'state_change',
                                f'run {r["id"]}: running → cancelled (verify fix, 孤儿 run)',
                                step_id=r['step_id'], run_id=r['id'])
                    finding['fixed'] = True
            # V4 伪造指纹：≥3 个步骤共享同一段非空 result_summary（直刷脚本常粘贴同一段话）
            counter: dict[str, int] = {}
            for s in steps:
                if s['result_summary']:
                    counter[s['result_summary']] = counter.get(s['result_summary'], 0) + 1
            for summ, n in counter.items():
                if n >= 3:
                    findings.append({'rule': 'V4', 'level': 'warn', 'kind': 'task', 'id': task_id,
                                     'detail': f'{n} 个步骤共享同一 result_summary（伪造指纹）'
                                               f'：{summ[:80]}', 'fixed': None})
            # V5 收口失真：任务 completed 但存在未终态步骤
            if task['status'] == 'completed':
                bad = [s['id'] for s in steps if s['status'] in ('pending', 'running', 'failed')]
                if bad:
                    finding = {'rule': 'V5', 'level': 'error', 'kind': 'task', 'id': task_id,
                               'detail': f'task completed 但存在未终态步骤: {bad}', 'fixed': None}
                    findings.append(finding)
                    if fix:
                        cur.execute(
                            f'UPDATE {t_task} SET status = %s, finished_at = NULL, '
                            f'updated_at = %s WHERE id = %s AND status = %s',
                            ('running', now, task_id, 'completed'))
                        self._event(cur, task_id, 'state_change',
                                    'task: completed → running (verify fix, 收口失真回退)')
                        finding['fixed'] = True
            if fix and any(f['fixed'] for f in findings):
                n_fixed = sum(1 for f in findings if f['fixed'])
                self._event(cur, task_id, 'note', f'verify fix: 修复 {n_fixed} 处不一致',
                            level='warn')
                self._touch_heartbeat(cur, task_id)
        if findings:
            log.info("verify task=%s 发现 %d 处异常%s", task_id, len(findings),
                     '，已修复' if fix else '')
        return findings
    # endregion

    # region ======== 产物 / 事件 ========
    def add_artifact(
        self,
        task_id: int,
        art_type: str,
        path: str,
        step_id: int | None = None,
        note: str | None = None,
    ) -> int:
        if art_type not in VALID_ART_TYPES:
            raise ValueError(f"非法的 art_type: {art_type!r}（合法值: {sorted(VALID_ART_TYPES)}）")
        with self._tx() as (_conn, cur):
            cur.execute(
                f'INSERT INTO {self._t("ai_task_artifact")} '
                f'(task_id, step_id, art_type, path, note, created_at) VALUES (%s,%s,%s,%s,%s,%s)',
                (task_id, step_id, art_type, path, note, datetime.now()))
            art_id = self._lastrowid(cur)
            self._event(cur, task_id, 'artifact', f'产物登记: {path} ({art_type})',
                        step_id=step_id)
        return art_id

    def list_artifacts(self, task_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_artifact")} WHERE task_id = %s ORDER BY id',
            (task_id,))

    def add_event(
        self,
        task_id: int,
        event_type: str,
        message: str,
        level: str = 'info',
        step_id: int | None = None,
        run_id: int | None = None,
    ) -> int:
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(
                f"非法的 event_type: {event_type!r}（合法值: {sorted(VALID_EVENT_TYPES)}）")
        if level not in VALID_EVENT_LEVELS:
            raise ValueError(f"非法的 level: {level!r}（合法值: {sorted(VALID_EVENT_LEVELS)}）")
        with self._tx() as (_conn, cur):
            cur.execute(
                f'INSERT INTO {self._t("ai_task_event")} '
                f'(task_id, step_id, run_id, event_type, level, message, created_at) '
                f'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (task_id, step_id, run_id, event_type, level, message, datetime.now()))
            return self._lastrowid(cur)

    def list_events(self, task_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_event")} WHERE task_id = %s '
            f'ORDER BY id DESC LIMIT %s', (task_id, limit))
    # endregion


#: 模块级默认管理器实例：注册一个指向 rdb 默认实例的 MySqlPlanTaskService
task_mgr: PlanTaskManager = PlanTaskManager()
task_mgr.register(PlanTaskManager.DEFAULT_NAME, MySqlPlanTaskService())

__all__ = ['MySqlPlanTaskService', 'task_mgr']

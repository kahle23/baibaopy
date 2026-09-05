"""
计划任务 MySQL 表结构定义（单一信息源）。

集中承载 ``ai_task_*`` 五张表的列定义、索引/唯一键与表注释，并提供 DDL 生成
函数 :func:`ddl`——列清单只此一份，建表语句由此生成，避免重复维护
（对标 ``baibao.ai_agent.memory`` 的 ``_COLUMNS`` 做法）。

本期仅 MySQL 方言；未来扩展其他方言时在列元组中追加定义列即可。
"""

_COLUMNS_INSTANCE: list[tuple[str, str, str]] = [
    ('id',                    'BIGINT AUTO_INCREMENT PRIMARY KEY', '主键，自增'),
    ('template_id',           'BIGINT DEFAULT NULL',               '来源标识 id（上层自定义）；NULL=无来源'),
    ('parent_task_id',        'BIGINT DEFAULT NULL',               '父任务 id；NULL=顶层'),
    ('title',                 'VARCHAR(255) NOT NULL',             '任务标题'),
    ('goal',                  'MEDIUMTEXT NOT NULL',               '任务目标（喂给编排层的总指令）'),
    ('status',                "VARCHAR(16) NOT NULL DEFAULT 'pending'", 'pending/running/paused/completed/failed/cancelled'),
    ('params',                'JSON DEFAULT NULL',                 '任务参数（模板实例化/自定义）'),
    ('max_retries',           'INT NOT NULL DEFAULT 1',            '步骤默认重试预算（步骤未指定时继承）'),
    ('heartbeat_at',          'DATETIME DEFAULT NULL',             '最近心跳；超时判僵尸'),
    ('heartbeat_timeout_sec', 'INT NOT NULL DEFAULT 1800',         '心跳超时阈值秒；超过判僵尸'),
    ('timeout_sec',           'INT DEFAULT NULL',                  '任务总超时秒；NULL=不限'),
    ('created_by',            'VARCHAR(64) DEFAULT NULL',          '创建者标识（标签，不鉴权）'),
    ('started_at',            'DATETIME DEFAULT NULL',             '首次 claim 时间'),
    ('finished_at',           'DATETIME DEFAULT NULL',             '终态时间（completed/failed/cancelled）'),
    ('created_at',            'DATETIME NOT NULL',                 '创建时间'),
    ('updated_at',            'DATETIME NOT NULL',                 '更新时间'),
]

_COLUMNS_STEP: list[tuple[str, str, str]] = [
    ('id',             'BIGINT AUTO_INCREMENT PRIMARY KEY',     '主键，自增'),
    ('task_id',        'BIGINT NOT NULL',                       '所属任务'),
    ('seq',            'INT NOT NULL',                          '执行顺序，从 1 起，同任务内唯一'),
    ('name',           'VARCHAR(255) NOT NULL',                 '步骤名'),
    ('step_type',      "VARCHAR(32) NOT NULL DEFAULT 'agent'",  'agent/bash/human_approval/condition'),
    ('instruction',    'MEDIUMTEXT NOT NULL',                   '该步骤完整指令（prompt/命令）'),
    ('status',         "VARCHAR(16) NOT NULL DEFAULT 'pending'", 'pending/running/succeeded/failed/skipped'),
    ('retry_count',    'INT NOT NULL DEFAULT 0',                '已重试次数'),
    ('max_retries',    'INT NOT NULL DEFAULT 1',                '最大重试次数（含首次共 max_retries+1 次机会）'),
    ('timeout_sec',    'INT DEFAULT NULL',                      '单步超时秒；NULL=不限'),
    ('depends_on',     'TEXT DEFAULT NULL',                     '依赖的同任务更早步骤 seq 列表（JSON 数组）；NULL=无显式依赖，claim 依赖感知模式依据'),
    ('result_summary', 'TEXT DEFAULT NULL',                     '执行结果摘要（续跑会话的上下文来源）'),
    ('started_at',     'DATETIME DEFAULT NULL',                 '首次 claim 时间'),
    ('finished_at',    'DATETIME DEFAULT NULL',                 '终态时间'),
    ('created_at',     'DATETIME NOT NULL',                     '创建时间'),
    ('updated_at',     'DATETIME NOT NULL',                     '更新时间'),
]

_COLUMNS_RUN: list[tuple[str, str, str]] = [
    ('id',             'BIGINT AUTO_INCREMENT PRIMARY KEY',     '主键，自增'),
    ('task_id',        'BIGINT NOT NULL',                       '所属任务（冗余，便于按任务查）'),
    ('step_id',        'BIGINT NOT NULL',                       '所属步骤'),
    ('session_id',     'VARCHAR(128) DEFAULT NULL',             '执行会话标识（哪个 AI 会话跑的）'),
    ('agent_name',     'VARCHAR(64) DEFAULT NULL',              'agent 外壳标识（标签）'),
    ('status',         "VARCHAR(16) NOT NULL DEFAULT 'running'", 'running/succeeded/failed/timeout/cancelled'),
    ('input_snapshot', 'MEDIUMTEXT DEFAULT NULL',               '本次输入快照（含续跑上下文包，便于复现）'),
    ('output',         'MEDIUMTEXT DEFAULT NULL',               '执行输出（子代理返回的原文）'),
    ('error_msg',      'TEXT DEFAULT NULL',                     '失败原因'),
    ('token_usage',    'INT DEFAULT NULL',                      '本次 token 消耗（可选回填）'),
    ('started_at',     'DATETIME NOT NULL',                     '开始时间'),
    ('finished_at',    'DATETIME DEFAULT NULL',                 '结束时间'),
]

_COLUMNS_ARTIFACT: list[tuple[str, str, str]] = [
    ('id',         'BIGINT AUTO_INCREMENT PRIMARY KEY',         '主键，自增'),
    ('task_id',    'BIGINT NOT NULL',                           '所属任务'),
    ('step_id',    'BIGINT DEFAULT NULL',                       '所属步骤；NULL=任务级产物'),
    ('art_type',   "VARCHAR(32) NOT NULL DEFAULT 'file'",       'file/report/diff/log/other'),
    ('path',       'VARCHAR(512) NOT NULL',                     '产物路径（相对仓库根或绝对路径）'),
    ('note',       'VARCHAR(255) DEFAULT NULL',                 '备注'),
    ('created_at', 'DATETIME NOT NULL',                         '创建时间'),
]

_COLUMNS_EVENT: list[tuple[str, str, str]] = [
    ('id',         'BIGINT AUTO_INCREMENT PRIMARY KEY',         '主键，自增'),
    ('task_id',    'BIGINT NOT NULL',                           '所属任务'),
    ('step_id',    'BIGINT DEFAULT NULL',                       '关联步骤；可空'),
    ('run_id',     'BIGINT DEFAULT NULL',                       '关联执行；可空'),
    ('event_type', 'VARCHAR(32) NOT NULL',                      'state_change/error/checkpoint/note/artifact'),
    ('level',      "VARCHAR(8) NOT NULL DEFAULT 'info'",        'info/warn/error'),
    ('message',    'TEXT NOT NULL',                             '事件内容'),
    ('created_at', 'DATETIME NOT NULL',                         '创建时间'),
]

#: 表注册表：基名 → (列定义, 索引/唯一键, 表注释)；建表 DDL 由此生成
TABLES: dict[str, tuple[list[tuple[str, str, str]], list[str], str]] = {
    'ai_task_instance': (
        _COLUMNS_INSTANCE,
        [
            'KEY idx_ai_task_inst_status (status)',
            'KEY idx_ai_task_inst_zombie (status, heartbeat_at)',
            'KEY idx_ai_task_inst_template (template_id)',
            'KEY idx_ai_task_inst_parent (parent_task_id)',
        ],
        '计划任务',
    ),
    'ai_task_step': (
        _COLUMNS_STEP,
        [
            'UNIQUE KEY uk_ai_task_step_seq (task_id, seq)',
            'KEY idx_ai_task_step_status (task_id, status)',
        ],
        '计划任务步骤（计划）',
    ),
    'ai_task_run': (
        _COLUMNS_RUN,
        [
            'KEY idx_ai_task_run_step (step_id)',
            'KEY idx_ai_task_run_task (task_id)',
            'KEY idx_ai_task_run_status (status)',
        ],
        '计划任务执行记录（尝试）',
    ),
    'ai_task_artifact': (
        _COLUMNS_ARTIFACT,
        [
            'KEY idx_ai_task_art_task (task_id)',
            'KEY idx_ai_task_art_step (step_id)',
        ],
        '计划任务产物',
    ),
    'ai_task_event': (
        _COLUMNS_EVENT,
        ['KEY idx_ai_task_event_task (task_id, id)'],
        '计划任务事件日志',
    ),
}


def sql_str(s: str) -> str:
    """转 SQL 单引号字符串字面量（``'`` → ``''`` 转义）。"""
    return "'" + s.replace("'", "''") + "'"


def ddl(base: str, table: str) -> str:
    """
    生成单表建表语句（MySQL 方言，utf8mb4 + 列/表注释 + 索引内联）。

    Args:
        base: 表基名（``_TABLES`` 的键，如 ``ai_task_instance``）。
        table: 实际表名（已拼前缀）。
    """
    cols, keys, comment = TABLES[base]
    col_lines = [f'    {c[0]} {c[1]} COMMENT {sql_str(c[2])}' for c in cols]
    key_lines = [f'    {k}' for k in keys]
    body = ',\n'.join(col_lines + key_lines)
    return (f'CREATE TABLE IF NOT EXISTS {table} (\n{body}\n) '
            f'CHARACTER SET utf8mb4 COMMENT={sql_str(comment)}')

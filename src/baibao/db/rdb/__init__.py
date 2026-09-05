"""
SQL 数据库模块。

基于 pykunlun 的 RdbClient / RdbManager 与 RdbBackupService 抽象层，
提供驱动客户端、备份（转储）/恢复服务实现，以及模块级默认管理器实例
:data:`rdb_mgr`，用户直接拿它按名称注册 client、按 db_type 注册备份服务。

sqlite 驱动复用 pykunlun 内置的 :class:`~pykunlun.db.SqliteClient`（connect-per-call）；
sqlite 备份使用内置 sqlite3 的 :class:`~pykunlun.db.SqliteBackupService`。

驱动与备份统一收敛在 :class:`~pykunlun.db.RdbManager`：client 实例走 ``name`` 索引
（query/execute），备份服务走 ``db_type`` 索引、``cfg`` 作参数（dump/restore）。

典型用法::

    from baibao.db.rdb import rdb_mgr, MysqlClient, RdbCfg

    # 驱动：为不同环境注册同类型的 MySQL 实例（实现类相同、连接配置不同）
    rdb_mgr.register("dev",  MysqlClient(RdbCfg(db_type='mysql', host='dev-host', ...)))
    rdb_mgr.register("test", MysqlClient(RdbCfg(db_type='mysql', host='test-host', ...)))

    # 按别名选择实例执行（省略 name 时用默认名 "default"）
    rdb_mgr.execute("INSERT INTO t VALUES (1)", name="dev")
    rows = rdb_mgr.query("SELECT * FROM t", name="test")

    # 备份：默认已注册 mysql/postgresql/sqlite 备份服务，直接传 cfg 执行
    rdb_mgr.dump(cfg, './backups/mysql_mydb.sql.gz')
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from pykunlun.db import (
    RdbBackupService,
    RdbCfg,
    RdbManager,
    SqliteBackupService,
    SqliteClient,
)
from pykunlun.util import ResolveType, fileutil

from .mysql_backup import MysqlBackupService
from .mysql_client import MysqlClient
from .pooled_client import PooledDBClient
from .postgresql_backup import PostgresqlBackupService
from .postgresql_client import PostgresqlClient

log = logging.getLogger(__name__)

# Python 3.12+ 弃用了 sqlite3 默认 datetime adapter，显式注册等价适配器
# （输出与原默认行为一致的 ISO 8601 字符串，仅影响 sqlite3 连接，MySQL/PostgreSQL 不受影响）
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())

# region ======== 配置加载 ========

_config_loaded = False


def _config_loader(manager: RdbManager, name: str) -> None:
    """配置加载器，注册客户端类并从 .baibao/rdb.config 加载配置，只执行一次。"""
    global _config_loaded
    if _config_loaded:
        return
    _config_loaded = True

    # 注册客户端类
    manager.register_client_class(MysqlClient)
    manager.register_client_class(PostgresqlClient)
    manager.register_client_class(SqliteClient)

    # 从配置文件加载实例
    try:
        content = fileutil.read_text(".baibao/rdb.config",
                           search_dirs=[ResolveType.CURRENT, ResolveType.USER])
        config: dict[str, dict[str, Any]] = json.loads(content)
        for n, cfg_data in config.items():
            cfg = RdbCfg(**cfg_data)
            manager.register(n, cfg)
    except Exception as e:
        log.warning("Failed to load rdb config: %s", e)


# endregion


# region ======== 模块级管理器实例 ========

#: 模块级默认管理器实例：按名称（别名）管理各数据库客户端实例，
#: 同时承载备份服务注册表（按 db_type），经 dump/restore 方法备份/恢复。
rdb_mgr: RdbManager = RdbManager(config_loader=_config_loader)

# 注册内置备份服务（无状态策略对象，模块级立即注册）
rdb_mgr.register_backup_service(MysqlBackupService())
rdb_mgr.register_backup_service(PostgresqlBackupService())
rdb_mgr.register_backup_service(SqliteBackupService())

# endregion


__all__ = [
    'MysqlBackupService',
    'MysqlClient',
    'PooledDBClient',
    'PostgresqlBackupService',
    'PostgresqlClient',
    'RdbBackupService',
    'RdbCfg',
    'RdbManager',
    'SqliteBackupService',
    'SqliteClient',
    'rdb_mgr',
]

"""
PostgreSQL 驱动客户端（psycopg2，连接池）。

继承 :class:`~baibao.db.rdb.pooled_client.PooledDBClient`，驱动模块由本类
通过 :func:`pykunlun.modutil.import_module` 自行导入与按需安装。
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any, ClassVar

from pykunlun.util import modutil

from .pooled_client import PooledDBClient


class PostgresqlClient(PooledDBClient):
    """
    PostgreSQL 驱动客户端（psycopg2，连接池）。
    """

    db_type = 'postgresql'

    #: 默认转换器：psycopg2 将 NUMERIC/DECIMAL 列默认映射为 :class:`~decimal.Decimal`，
    #: 默认转 :class:`float`，以适配 JSON 序列化等不原生支持 Decimal 的场景。
    DEFAULT_CONVERTERS: ClassVar[dict[type, Callable[[Any], Any]] | None] = {Decimal: float}

    def _validate_and_prepare_cfg(self) -> None:
        # PostgreSQL 标准默认：端口 5432、字符集 utf8；先填再交基类校验。
        if self.cfg.port is None:
            self.cfg.port = 5432
        if not self.cfg.charset:
            self.cfg.charset = 'utf8'
        super()._validate_and_prepare_cfg()

    def get_driver(self) -> Any:
        return modutil.import_module('psycopg2', 'psycopg2')

    def build_connect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            'host': self.cfg.host,
            'port': self.cfg.port,
            'user': self.cfg.username,
            'password': self.cfg.password,
            'database': self.cfg.database,
        }
        if self.cfg.charset:
            # psycopg2 不支持 charset 参数，通过 client_encoding 设置
            kwargs['client_encoding'] = self.cfg.charset
        return kwargs

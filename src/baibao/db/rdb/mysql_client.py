"""
MySQL 驱动客户端（pymysql，连接池）。

继承 :class:`~baibao.db.rdb.pooled_client.PooledDBClient`，驱动模块由本类
通过 :func:`pykunlun.modutil.import_module` 自行导入与按需安装。
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any, ClassVar

from pykunlun.util import modutil

from .pooled_client import PooledDBClient


class MysqlClient(PooledDBClient):
    """
    MySQL 驱动客户端（pymysql，连接池）。
    """

    db_type = 'mysql'

    #: 默认转换器：pymysql 将 DECIMAL 列返回为 :class:`~decimal.Decimal`，
    #: 默认转 :class:`float`，以适配 JSON 序列化等不原生支持 Decimal 的场景。
    DEFAULT_CONVERTERS: ClassVar[dict[type, Callable[[Any], Any]] | None] = {Decimal: float}

    def _validate_and_prepare_cfg(self) -> None:
        # MySQL 标准默认：端口 3306、字符集 utf8mb4；先填再交基类校验。
        if self.cfg.port is None:
            self.cfg.port = 3306
        if not self.cfg.charset:
            self.cfg.charset = 'utf8mb4'
        super()._validate_and_prepare_cfg()

    def get_driver(self) -> Any:
        return modutil.import_module('pymysql', 'pymysql')

    def build_connect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            'host': self.cfg.host,
            'port': self.cfg.port,
            'user': self.cfg.username,
            'password': self.cfg.password,
            'database': self.cfg.database,
        }
        if self.cfg.charset:
            kwargs['charset'] = self.cfg.charset
        return kwargs

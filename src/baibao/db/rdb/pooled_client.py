"""
带连接池的 RdbClient 基类。

基于 pykunlun 的 :class:`~pykunlun.db.RdbClient`，覆盖 :meth:`get_connection`
走连接池（DBUtils.PooledDB）或单连接，使继承的 query / execute 自动获得
连接复用能力。子类需实现 :meth:`~pykunlun.db.RdbClient.get_driver` 与
:meth:`~pykunlun.db.RdbClient.build_connect_kwargs`。
"""

import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any, ClassVar

from pykunlun.db import RdbClient
from pykunlun.util import logutil, modutil

log = logutil.getLogger(__name__)


class _SingleConnectionProxy:
    """
    单连接模式代理包装器。

    包装单连接模式下的数据库连接，使调用方的 ``close()`` 不会真正关闭底层连接，
    而是交由 :class:`PooledDBClient` 统一管理连接的生命周期。
    其他属性与方法调用均透传给底层连接。
    """

    def __init__(self, connection: Any) -> None:
        object.__setattr__(self, '_connection', connection)

    def close(self) -> None:
        """
        空操作，不关闭底层连接。
        """

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._connection, name, value)


class PooledDBClient(RdbClient):
    """
    带连接池的 :class:`~pykunlun.db.RdbClient` 基类。

    覆盖 :meth:`get_connection` 走连接池（或单连接），使继承自基类的
    :meth:`~pykunlun.db.RdbClient.query` / :meth:`~pykunlun.db.RdbClient.execute`
    自动获得连接复用能力，无需重复实现。

    子类仍需实现 :meth:`~pykunlun.db.RdbClient.get_driver` 与
    :meth:`~pykunlun.db.RdbClient.build_connect_kwargs`。

    支持两种模式：
      - 连接池模式（默认）：基于 DBUtils.PooledDB，线程安全；
      - 单连接模式：直接复用单个连接，**非线程安全，仅限单线程**。

    默认转换器：子类可通过覆盖 :attr:`DEFAULT_CONVERTERS` 为常用驱动返回类型
    注册默认转换（如 MySQL/PostgreSQL 的 :class:`~decimal.Decimal` → :class:`float`）；
    :meth:`query` 在调用方未显式传入 ``converters``（``None``）时采用之。
    """

    #: :meth:`query` 的默认值转换器映射，``None`` 表示不做任何转换。
    #: 子类按驱动返回类型覆盖（如 ``{Decimal: float}``）。仅在调用方未传 ``converters`` 时生效。
    DEFAULT_CONVERTERS: ClassVar[dict[type, Callable[[Any], Any]] | None] = None

    def __init__(self, cfg: Any, use_pool: bool = True, mincached: int = 1,
                 maxcached: int = 10, maxconnections: int = 20) -> None:
        """
        Args:
            cfg: 数据库配置对象。
            use_pool: 是否使用连接池模式，默认 True。
            mincached: 连接池最小空闲连接数，默认 1。
            maxcached: 连接池最大空闲连接数，默认 10。
            maxconnections: 连接池最大总连接数，默认 20。
        """
        # 先暂存池参数，再调用 super().__init__（其会触发 _validate_cfg），
        # 避免 _init_connection_source 在属性就绪前被调用。
        self.use_pool = use_pool
        self.mincached = mincached
        self.maxcached = maxcached
        self.maxconnections = maxconnections
        self._pool: Any = None
        self._connection = None
        self._init_lock = threading.RLock()
        super().__init__(cfg)
        self._connection_created = False
        log.debug("数据库客户端已注册（懒加载，use_pool:%s），地址：%s://%s:%s/%s",
                  use_pool, cfg.db_type, cfg.host, cfg.port, cfg.database)

    def _init_connection_source(self) -> None:
        """
        初始化连接池或单连接。
        """
        if self.use_pool:
            try:
                PooledDB = modutil.import_module('dbutils.pooled_db', 'dbutils').PooledDB
            except ImportError:
                PooledDB = modutil.import_module('DBUtils.PooledDB').PooledDB
            pool_kwargs: dict[str, Any] = {
                'creator': self.get_driver(),
                'mincached': self.mincached,
                'maxcached': self.maxcached,
                'maxconnections': self.maxconnections,
            }
            pool_kwargs.update(self.build_connect_kwargs())
            self._pool = PooledDB(**pool_kwargs)
        else:
            # 单连接模式：先关闭旧连接再创建新连接，避免资源泄漏
            if self._connection is not None and self.is_connection_open(self._connection):
                try:
                    self._connection.close()
                except Exception:
                    pass
            self._connection = self.get_driver().connect(**self.build_connect_kwargs())

    def get_connection(self) -> Any:
        """
        获取数据库连接（懒加载：首次调用时才创建连接池/连接，线程安全）。

        连接池模式：从池中获取一个连接，使用后调用其 ``close()`` 归还到池中。
        单连接模式：返回 :class:`_SingleConnectionProxy` 代理，其 ``close()`` 为空操作，
        防止调用方误关共享连接。
        """
        if not self._connection_created:
            with self._init_lock:
                if not self._connection_created:
                    self._init_connection_source()
                    self._connection_created = True
                    log.info("数据库连接已创建（use_pool:%s），地址：%s://%s:%s/%s",
                             self.use_pool, self.cfg.db_type, self.cfg.host, self.cfg.port, self.cfg.database)

        if self.use_pool:
            return self._pool.connection()
        if self._connection is None or not self.is_connection_open(self._connection):
            with self._init_lock:
                if self._connection is None or not self.is_connection_open(self._connection):
                    self._init_connection_source()
        return _SingleConnectionProxy(self._connection)

    def query(self, sql: str, params: tuple[Any, ...] | None = None,
              converters: dict[type, Callable[[Any], Any]] | None = None) -> list[dict[str, Any]]:
        """
        执行查询（连接池/单连接版）。

        与 :meth:`~pykunlun.db.RdbClient.query` 一致，额外约定：当调用方未传入
        ``converters``（``None``）时，采用 :attr:`DEFAULT_CONVERTERS` 作为默认转换器
        （便于子类为特定驱动注册默认转换，如 ``Decimal → float``）；
        显式传入 ``{}`` 则表示本次**不做任何转换**。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            converters: 值转换器映射；``None`` 用 :attr:`DEFAULT_CONVERTERS`，
                ``{}`` 表示不转换。

        Returns:
            查询结果列表，每个元素是一个字典，键为列名。
        """
        if converters is None:
            converters = self.DEFAULT_CONVERTERS
        return super().query(sql, params, converters)

    def close(self) -> None:
        """
        关闭连接池（或单连接），释放资源。
        """
        if self.use_pool:
            if self._pool is not None:
                self._pool.close()
                self._pool = None
        else:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    # 返回类型用类名字符串而非 Self：基线 py3.10 无 typing.Self（PYI034 特例豁免）
    def __enter__(self) -> "PooledDBClient":  # noqa: PYI034
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc_val: BaseException | None, exc_tb: TracebackType | None) -> None:
        self.close()

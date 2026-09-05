import os
import sqlite3
import tempfile
import unittest

from baibao.db.rdb import RdbCfg, SqliteClient, rdb_mgr


def _reset_registry() -> None:
    """清空模块级管理器的所有已注册实例。"""
    for name in list(rdb_mgr.get_registered_names()):
        rdb_mgr.unregister(name)


class _RdbTestBase(unittest.TestCase):
    """rdb 管理器测试基类：每个用例使用独立的临时 sqlite 文件库。"""

    def setUp(self) -> None:
        _reset_registry()
        self._fd, self._db_path = tempfile.mkstemp(suffix='.db')
        os.close(self._fd)
        # 注册为默认实例（SqliteClient 来自 pykunlun，connect-per-call）
        rdb_mgr.register("default", SqliteClient(RdbCfg(db_type='sqlite', database=self._db_path)))

    def tearDown(self) -> None:
        _reset_registry()
        try:
            os.remove(self._db_path)
        except OSError:
            pass


class TestExecute(_RdbTestBase):
    """测试 manager.execute - 执行 SQL 语句（INSERT、UPDATE、DELETE）"""

    def test_execute_success(self) -> None:
        """执行成功并返回受影响行数"""
        rdb_mgr.execute("CREATE TABLE test (id INTEGER)")
        n = rdb_mgr.execute("INSERT INTO test VALUES (1)")
        self.assertEqual(n, 1)
        self.assertEqual(rdb_mgr.query("SELECT COUNT(*) AS c FROM test"), [{'c': 1}])

    def test_execute_rollback_on_error(self) -> None:
        """出错时回滚，数据不残留"""
        rdb_mgr.execute("CREATE TABLE u (id INTEGER PRIMARY KEY)")
        rdb_mgr.execute("INSERT INTO u VALUES (1)")
        with self.assertRaises(sqlite3.IntegrityError):
            rdb_mgr.execute("INSERT INTO u VALUES (1)")  # 主键冲突
        # 回滚后仍为 1 行
        self.assertEqual(rdb_mgr.query("SELECT COUNT(*) AS c FROM u"), [{'c': 1}])


class TestQuery(_RdbTestBase):
    """测试 manager.query - 执行查询语句"""

    def test_query_success(self) -> None:
        """查询成功并返回 list[dict]"""
        rdb_mgr.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        rdb_mgr.execute("INSERT INTO users VALUES (1, 'test')")
        result = rdb_mgr.query("SELECT * FROM users")
        self.assertEqual(result, [{'id': 1, 'name': 'test'}])

    def test_query_with_params(self) -> None:
        """带参数的查询"""
        rdb_mgr.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        rdb_mgr.execute("INSERT INTO users VALUES (1, 'a')")
        rdb_mgr.execute("INSERT INTO users VALUES (2, 'b')")
        result = rdb_mgr.query("SELECT * FROM users WHERE id = ?", (1,))
        self.assertEqual(result, [{'id': 1, 'name': 'a'}])


class TestNamedInstances(_RdbTestBase):
    """测试按别名注册多个实例"""

    def test_named_instances_isolation(self) -> None:
        """不同别名的实例彼此隔离"""
        fd, p2 = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            rdb_mgr.register("other", SqliteClient(RdbCfg(db_type='sqlite', database=p2)))
            rdb_mgr.execute("CREATE TABLE t (id INTEGER)", name="default")
            rdb_mgr.execute("INSERT INTO t VALUES (1)", name="default")
            # default 有 t 表，other 没有
            self.assertEqual(rdb_mgr.query("SELECT COUNT(*) AS c FROM t"), [{'c': 1}])
            tables = rdb_mgr.query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='t'",
                name="other",
            )
            self.assertEqual(tables, [])
        finally:
            try:
                os.remove(p2)
            except OSError:
                pass


if __name__ == '__main__':
    unittest.main()

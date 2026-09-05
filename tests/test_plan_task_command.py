"""plan_task 命令的单元测试：配置加载与身份字段解析（纯文件逻辑，不碰数据库）。"""

import argparse
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from baibao.cli import plan_task_command as ptc


@contextmanager
def _scoped_env(**kwargs: str | None):
    """临时增删环境变量（值为 None 表示确保不存在），退出时还原。"""
    originals = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in originals.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def _config_env(cwd_files: dict[str, str], home_files: dict[str, str] | None = None):
    """隔离 _load_config 的两个搜索目录：cwd 放 cwd_files，home（expanduser）放 home_files。

    同时清空模块级配置缓存，保证探测的是本次给定的目录。
    """
    with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as home:
        for name, content in cwd_files.items():
            p = Path(cwd, '.baibao', name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')
        for name, content in (home_files or {}).items():
            p = Path(home, '.baibao', name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')
        saved_cache = ptc._config_cache
        saved_cwd = os.getcwd()
        ptc._config_cache = None
        os.chdir(cwd)
        try:
            with mock.patch('os.path.expanduser', return_value=home), \
                 _scoped_env(PLAN_TASK_DB=None, PLAN_TASK_OWNER=None,
                             PLAN_TASK_SESSION_ID=None, PLAN_TASK_AGENT_NAME=None):
                yield
        finally:
            os.chdir(saved_cwd)
            ptc._config_cache = saved_cache


class TestLoadConfig(unittest.TestCase):
    """_load_config：只认 plan_task.config，旧名 agent_task.config 不再回退。"""

    def test_plan_task_config_loaded(self) -> None:
        with _config_env({'plan_task.config': '{"rdb_name": "demo", "owner": "u1"}'}):
            self.assertEqual(ptc._load_config(), {'rdb_name': 'demo', 'owner': 'u1'})

    def test_agent_task_config_ignored(self) -> None:
        with _config_env({'agent_task.config': '{"rdb_name": "old"}'}):
            self.assertEqual(ptc._load_config(), {})

    def test_cwd_wins_over_home(self) -> None:
        with _config_env({'plan_task.config': '{"rdb_name": "cwd"}'},
                         {'plan_task.config': '{"rdb_name": "home"}'}):
            self.assertEqual(ptc._load_config()['rdb_name'], 'cwd')

    def test_home_fallback(self) -> None:
        with _config_env({}, {'plan_task.config': '{"rdb_name": "home"}'}):
            self.assertEqual(ptc._load_config()['rdb_name'], 'home')

    def test_bom_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            p = Path(cwd, '.baibao', 'plan_task.config')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'\xef\xbb\xbf{"rdb_name": "bom"}')
            saved_cache, saved_cwd = ptc._config_cache, os.getcwd()
            ptc._config_cache = None
            os.chdir(cwd)
            try:
                with mock.patch('os.path.expanduser', return_value=cwd + os.sep + 'nohome'):
                    self.assertEqual(ptc._load_config()['rdb_name'], 'bom')
            finally:
                os.chdir(saved_cwd)
                ptc._config_cache = saved_cache


class TestResolveSession(unittest.TestCase):
    """_resolve_session：身份字段只走 标志 > PLAN_TASK_* 环境变量，不读配置文件。"""

    @staticmethod
    def _ns(session_id: str | None = None, agent_name: str | None = None
            ) -> argparse.Namespace:
        return argparse.Namespace(session_id=session_id, agent_name=agent_name)

    def test_config_identity_keys_ignored(self) -> None:
        cfg = '{"rdb_name": "demo", "session_id": "cfg-sess", "agent_name": "cfg-agent"}'
        with _config_env({'plan_task.config': cfg}):
            self.assertEqual(ptc.PlanTaskCommand._resolve_session(self._ns()),
                             (None, None))

    def test_env_used_when_no_flag(self) -> None:
        with _config_env({}), \
             _scoped_env(PLAN_TASK_SESSION_ID='env-sess', PLAN_TASK_AGENT_NAME='env-agent'):
            self.assertEqual(ptc.PlanTaskCommand._resolve_session(self._ns()),
                             ('env-sess', 'env-agent'))

    def test_flag_wins_over_env(self) -> None:
        with _config_env({}), \
             _scoped_env(PLAN_TASK_SESSION_ID='env-sess', PLAN_TASK_AGENT_NAME='env-agent'):
            ns = self._ns(session_id='flag-sess', agent_name='flag-agent')
            self.assertEqual(ptc.PlanTaskCommand._resolve_session(ns),
                             ('flag-sess', 'flag-agent'))


class TestResolveCreatedBy(unittest.TestCase):
    """_resolve_created_by：owner 仍可来自配置文件，rdb/owner 不受旧名影响。"""

    def test_owner_from_config(self) -> None:
        with _config_env({'plan_task.config': '{"owner": "cfg-owner"}'}):
            ns = argparse.Namespace(created_by=None)
            self.assertEqual(ptc.PlanTaskCommand._resolve_created_by(ns), 'cfg-owner')


if __name__ == '__main__':
    unittest.main()

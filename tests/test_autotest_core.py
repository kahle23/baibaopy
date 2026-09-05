"""core 层与 probe 参数对象的单元测试：polling / envutil / devtools / ProbeOptions / 兼容 shim。

全部用 stub 对象（不启动浏览器）：devtools 用假 CDP 会话与假 mouse 记录
调用序列做断言；polling 用小超时快跑。
"""

import os
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import baibao.autotest as at
from baibao.autotest.core.devtools import engine_name, new_session, real_click
from baibao.autotest.core.envutil import load_dotenv_if_present, normalize_base_url
from baibao.autotest.core.login_state import auth_state_path
from baibao.autotest.core.polling import poll_until
from baibao.autotest.probe import ProbeOptions, run_probe
from baibao.autotest.probe import runner as probe_runner

if TYPE_CHECKING:
    from playwright.sync_api import Page


class TestPollUntil(unittest.TestCase):
    """poll_until：通用轮询。"""

    def test_immediate_truthy(self) -> None:
        self.assertEqual(poll_until(lambda: 42, timeout_ms=100), 42)

    def test_becomes_truthy(self) -> None:
        state = {"n": 0}

        def fn() -> int | None:
            state["n"] += 1
            return state["n"] >= 3 or None

        self.assertTrue(poll_until(fn, timeout_ms=2000, interval_ms=5))
        self.assertGreaterEqual(state["n"], 3)

    def test_timeout_returns_last_falsy(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)

        result = poll_until(fn, timeout_ms=60, interval_ms=20)
        self.assertIsNone(result)
        self.assertGreaterEqual(len(calls), 2)  # 至少执行一轮，超时前有间隔

    def test_custom_sleep_ms(self) -> None:
        slept: list[int] = []
        poll_until(lambda: None, timeout_ms=30, interval_ms=10, sleep_ms=slept.append)
        self.assertIn(10, slept)


class TestEnvutil(unittest.TestCase):
    """normalize_base_url：末尾斜杠规范化。"""

    def test_strips_trailing_slashes(self) -> None:
        self.assertEqual(normalize_base_url("http://x.com/"), "http://x.com")
        self.assertEqual(normalize_base_url("http://x.com///"), "http://x.com")
        self.assertEqual(normalize_base_url("http://x.com"), "http://x.com")
        self.assertEqual(normalize_base_url(""), "")


class _FakeCdp:
    """记录 send 序列的假 CDP 会话。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.detached = False

    def send(self, method: str, params: dict[str, Any]) -> None:
        self.sent.append((method, params))

    def detach(self) -> None:
        self.detached = True


class _FakeMouse:
    """记录 move/down/up 序列的假 page.mouse。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))

    def down(self) -> None:
        self.calls.append(("down",))

    def up(self) -> None:
        self.calls.append(("up",))


class _FakePage:
    """最小假 Page：context.browser.browser_type.name 决定内核。"""

    def __init__(self, engine: str, browser_none: bool = False) -> None:
        self.mouse: Any = _FakeMouse()
        cdp = _FakeCdp()

        def _new_cdp_session(_page: Any) -> _FakeCdp:
            return cdp

        self.context = type(
            "Ctx", (), {
                "browser": None if browser_none else
                type("B", (), {"browser_type": type("T", (), {"name": engine})()})(),
                "cdp": cdp,
                "new_cdp_session": staticmethod(_new_cdp_session),
            },
        )()
        self._cdp = cdp


class TestDevtools(unittest.TestCase):
    """devtools：真实输入按内核分流 + CDP 会话守卫。"""

    def test_engine_name(self) -> None:
        self.assertEqual(engine_name(cast("Page", _FakePage("chromium"))), "chromium")
        self.assertEqual(engine_name(cast("Page", _FakePage("firefox"))), "firefox")
        self.assertEqual(engine_name(cast("Page", _FakePage("webkit"))), "webkit")

    def test_engine_name_browser_none_defaults_chromium(self) -> None:
        self.assertEqual(engine_name(cast("Page", _FakePage("x", browser_none=True))), "chromium")

    def test_real_click_chromium_sends_cdp_sequence(self) -> None:
        page = _FakePage("chromium")
        real_click(cast("Page", page), 10, 20)
        cdp = page._cdp  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(
            [p["type"] for _, p in cdp.sent],
            ["mouseMoved", "mousePressed", "mouseReleased"],
        )
        pressed = cdp.sent[1][1]
        self.assertEqual((pressed["x"], pressed["y"]), (10, 20))
        self.assertEqual(pressed["button"], "left")
        self.assertTrue(cdp.detached)

    def test_real_click_firefox_uses_mouse_sequence(self) -> None:
        page = _FakePage("firefox")
        real_click(cast("Page", page), 5, 6)
        self.assertEqual(page.mouse.calls, [("move", 5, 6), ("down",), ("up",)])
        self.assertEqual(page._cdp.sent, [])  # pyright: ignore[reportPrivateUsage]  # Firefox 不碰 CDP

    def test_new_session_non_chromium_rejected(self) -> None:
        for engine in ("firefox", "webkit"):
            with self.assertRaises(NotImplementedError):
                new_session(cast("Page", _FakePage(engine)))

    def test_new_session_chromium_ok(self) -> None:
        page = _FakePage("chromium")
        self.assertIs(new_session(cast("Page", page)), page._cdp)  # pyright: ignore[reportPrivateUsage]


class TestProbeOptions(unittest.TestCase):
    """ProbeOptions 参数对象与 run_probe 兼容委托。"""

    def test_defaults(self) -> None:
        opts = ProbeOptions(target="it-asset", base_url="http://x.com")
        self.assertEqual(opts.account, "admin")
        self.assertEqual(opts.auth_dir, ".auth")
        self.assertEqual(opts.settle_ms, 600)
        self.assertFalse(opts.headless)
        self.assertIsNone(opts.use_builtin_chromium)
        self.assertEqual(opts.click_labels, ())
        self.assertIsNone(opts.extract_js)

    def test_run_probe_delegates_to_options_entry(self) -> None:
        """旧签名 run_probe 委托 run_probe_with(ProbeOptions)，字段逐项透传。"""
        captured: dict[str, Any] = {}

        def fake_run_with(opts: Any) -> str:
            captured["opts"] = opts
            return "SUMMARY"

        with patch.object(probe_runner, "run_probe_with", fake_run_with):
            out = run_probe(
                "#/oa/asset", base_url="http://x.com/", account="ops",
                username="u", password="p", brief=True, click_labels=["供应商"],
            )
        self.assertEqual(out, "SUMMARY")
        opts = captured["opts"]
        self.assertEqual(opts.target, "#/oa/asset")
        self.assertEqual(opts.account, "ops")
        self.assertTrue(opts.brief)
        self.assertEqual((opts.username, opts.password), ("u", "p"))
        self.assertEqual(opts.click_labels, ["供应商"])


class TestEnvUtilFindEnvFile(unittest.TestCase):
    """load_dotenv_if_present 的查找顺序：显式路径 > cwd/.env > 逐级向上。"""

    KEY = "_BAIBAO_TEST_DOTENV_KEY"

    def setUp(self) -> None:
        self._old_cwd = Path.cwd()
        self._tmp = tempfile.mkdtemp(prefix="baibao_envutil_")
        os.environ.pop(self.KEY, None)

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        os.environ.pop(self.KEY, None)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_env(self, dir_path: Path, value: str) -> None:
        (dir_path / ".env").write_text(f"{self.KEY}={value}\n", encoding="utf-8")

    def test_walk_up_finds_parent_env(self) -> None:
        parent = Path(self._tmp); child = parent / "sub"; child.mkdir()
        self._write_env(parent, "from_parent")
        os.chdir(child)
        load_dotenv_if_present()
        self.assertEqual(os.environ.get(self.KEY), "from_parent")

    def test_cwd_takes_priority_over_parent(self) -> None:
        parent = Path(self._tmp); child = parent / "sub"; child.mkdir()
        self._write_env(parent, "from_parent"); self._write_env(child, "from_cwd")
        os.chdir(child)
        load_dotenv_if_present()
        self.assertEqual(os.environ.get(self.KEY), "from_cwd")

    def test_explicit_env_file_overrides_cwd(self) -> None:
        parent = Path(self._tmp); child = parent / "sub"; child.mkdir()
        explicit = parent / "custom.env"
        explicit.write_text(f"{self.KEY}=from_explicit\n", encoding="utf-8")
        self._write_env(child, "from_cwd")
        os.chdir(child)
        load_dotenv_if_present(explicit)
        self.assertEqual(os.environ.get(self.KEY), "from_explicit")

    def test_no_env_found_is_silent(self) -> None:
        os.chdir(self._tmp)
        load_dotenv_if_present()  # 无 .env：静默，不抛错
        self.assertIsNone(os.environ.get(self.KEY))


class TestAuthStatePathAccount(unittest.TestCase):
    """auth_state_path 按账号（登录态槽位）命名：<auth_dir>/<account>.json。"""

    def test_account_slot_naming(self) -> None:
        self.assertEqual(
            auth_state_path(Path(".auth"), "a"), Path(".auth") / "a.json",
        )
        self.assertEqual(
            auth_state_path(Path(".auth"), "admin"), Path(".auth") / "admin.json",
        )


class TestCompatShims(unittest.TestCase):
    """旧扁平模块路径的等价性（browser-e2e-runner 技能等外部依赖这些路径）。"""

    def test_shims_reexport_same_objects(self) -> None:
        from baibao.autotest import (
            browser as b_shim,
        )
        from baibao.autotest import (
            dom_summary as ds_shim,
        )
        from baibao.autotest import (
            login_state as ls_shim,
        )
        from baibao.autotest import (
            probe,
        )
        from baibao.autotest.core import browser as b_core
        from baibao.autotest.core import login_state as ls_core

        self.assertIs(b_shim.launch_browser, b_core.launch_browser)
        self.assertIs(ls_shim.LoginCfg, ls_core.LoginCfg)
        self.assertIs(ds_shim.run_probe, probe.run_probe)
        self.assertIs(ds_shim.format_summary, probe.format_summary)
        self.assertIs(ds_shim.EXTRACT_JS, probe.EXTRACT_JS)

    def test_facade_all_resolves(self) -> None:
        for name in at.__all__:
            self.assertTrue(hasattr(at, name), f"__all__ 中的 {name} 未能从包导入")


if __name__ == "__main__":
    unittest.main()

"""lowprofile 单元测试：human_pause / risk_wall_hit / wall_hit / wait_logged_in /
wait_wall_cleared / launch_browser_persistent（含 launch_browser 回退链回归）。

全部用 stub 对象（不启动浏览器）：stub chromium 记录每次启动尝试的 kwargs，
断言浏览器选择链顺序与低调运行默认值（有头 / 不模拟 viewport / 自动追加
AutomationControlled 参数）；等待类函数用可变状态的假 Page 快进。
"""

import os
import unittest
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from baibao.autotest.core.browser import launch_browser, launch_browser_persistent
from baibao.autotest.core.lowprofile import (
    human_pause,
    risk_wall_hit,
    wait_logged_in,
    wait_wall_cleared,
    wall_hit,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page, Playwright


# ---------- stub ----------
class _StubChromium:
    """记录全部启动尝试；可配置内置内核失败次数与特定 channel 失败。"""

    def __init__(self, *, fail_builtins: int = 0,
                 fail_channels: set[str] | tuple[str, ...] = (),
                 fail_paths: set[str] | tuple[str, ...] = ()) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.fail_builtins = fail_builtins
        self.fail_channels: set[str] = set(fail_channels)
        self.fail_paths: set[str] = set(fail_paths)

    def _try(self, kwargs: dict[str, Any], *, builtin: bool, key: str | None) -> None:
        self.attempts.append(dict(kwargs))
        if builtin and self.fail_builtins > 0:
            self.fail_builtins -= 1
            raise RuntimeError("stub: 内置 Chromium 缺失")
        if key in self.fail_channels or key in self.fail_paths:
            raise RuntimeError(f"stub: {key} 不可用")

    def launch(self, **kwargs: Any) -> tuple[str, None]:
        builtin = "channel" not in kwargs and "executable_path" not in kwargs
        key = kwargs.get("channel") or kwargs.get("executable_path")
        self._try(kwargs, builtin=builtin, key=key)
        return ("browser", None)

    def launch_persistent_context(self, **kwargs: Any) -> tuple[str, Any]:
        builtin = "channel" not in kwargs and "executable_path" not in kwargs
        key = kwargs.get("channel") or kwargs.get("executable_path")
        self._try(kwargs, builtin=builtin, key=key)
        return ("ctx", kwargs["user_data_dir"])


class _StubPlaywright:
    def __init__(self, chromium: _StubChromium) -> None:
        self.chromium = chromium


class _FakePage:
    """记录 wait_for_timeout 收到的毫秒数。"""

    def __init__(self) -> None:
        self.waits_ms: list[int] = []

    def wait_for_timeout(self, ms: int) -> None:
        self.waits_ms.append(ms)


class _FakeStatePage:
    """带 url/title/body/身份元素状态的假 Page；支持 reload 触发状态变化。"""

    def __init__(self, url: str = "", title: str = "", body: str = "",
                 identity_count: int = 0) -> None:
        self.url = url
        self.title_text = title
        self.body = body
        self.identity_count = identity_count
        self.reloads = 0
        self.waits_ms: list[int] = []

    def title(self) -> str:
        return self.title_text

    def inner_text(self, _sel: str) -> str:
        return self.body

    def reload(self, **_kw: Any) -> None:
        self.reloads += 1
        self.on_reload()

    def on_reload(self) -> None:
        pass  # 测试里按需覆写状态变化

    def locator(self, _sel: str) -> Any:
        page = self

        class _Loc:
            def count(self) -> int:
                return page.identity_count

        return _Loc()

    def wait_for_timeout(self, ms: int) -> None:
        self.waits_ms.append(ms)


# ---------- human_pause ----------
class TestHumanPause(unittest.TestCase):
    """human_pause：随机抖动等待，等待毫秒数与返回值一致。"""

    def test_pauses_random_seconds_and_returns_it(self) -> None:
        page = _FakePage()
        with patch("baibao.autotest.core.lowprofile.random.uniform", return_value=5.234) as mock_uniform:
            sec = human_pause(cast("Page", page))
        self.assertEqual(sec, 5.234)
        self.assertEqual(page.waits_ms, [5234])
        mock_uniform.assert_called_once_with(3.0, 8.0)  # 默认区间 3~8s

    def test_custom_bounds_forwarded(self) -> None:
        page = _FakePage()
        with patch("baibao.autotest.core.lowprofile.random.uniform", return_value=1.5) as mock_uniform:
            sec = human_pause(cast("Page", page), lo=1.0, hi=2.0)
        self.assertEqual(sec, 1.5)
        self.assertEqual(page.waits_ms, [1500])
        mock_uniform.assert_called_once_with(1.0, 2.0)


# ---------- risk_wall_hit ----------
class TestRiskWallHit(unittest.TestCase):
    """risk_wall_hit：验证墙特征词命中即返回，宁误停不硬闯。"""

    def test_normal_text_no_hit(self) -> None:
        body = "烧烤 人均¥102 口味很好 环境不错 营业中 10:00-22:00 推荐菜：烤茄子"
        self.assertIsNone(risk_wall_hit(body))

    def test_wall_text_hits(self) -> None:
        body = "为了你的账号安全，请依次点击下图中的图标完成验证"
        self.assertEqual(risk_wall_hit(body), "请依次点击")

    def test_slider_wall_hits(self) -> None:
        self.assertEqual(risk_wall_hit("请拖动滑块完成拼图"), "请拖动")

    def test_rate_limit_hits(self) -> None:
        self.assertEqual(risk_wall_hit("访问频率过高，请稍后再试"), "访问频率")

    def test_extra_keys_site_specific(self) -> None:
        body = "为了正常浏览，请完成汉字点选校验"  # 站点特有措辞，不在默认词表
        self.assertIsNone(risk_wall_hit(body))
        self.assertEqual(risk_wall_hit(body, extra_keys=["汉字点选"]), "汉字点选")

    def test_empty_text_is_none(self) -> None:
        self.assertIsNone(risk_wall_hit(""))

    def test_url_hijack_form_hits(self) -> None:
        # 整页 302 劫持到独立验证域：URL 比正文更早更稳，正文正常也命中
        self.assertEqual(risk_wall_hit("", url="https://verify.meituan.com/v2/app?x=1"), "verify.")
        self.assertEqual(risk_wall_hit("一切正常", url="https://verify.meituan.com/v2"), "verify.")
        self.assertEqual(risk_wall_hit("正常内容", url="https://a.com/captcha/show"), "captcha")
        self.assertIsNone(risk_wall_hit("正常内容", url="https://www.example.com/list"))

    def test_verify_center_in_default_keys(self) -> None:
        self.assertEqual(risk_wall_hit("验证中心"), "验证中心")


class TestWallHit(unittest.TestCase):
    """wall_hit：Page 级三层检测（URL 域名 → title → 正文头部）。"""

    def test_url_hijack(self) -> None:
        page = _FakeStatePage(url="https://verify.meituan.com/v2/app")
        self.assertEqual(wall_hit(cast("Page", page)), "verify.")

    def test_title_form(self) -> None:
        page = _FakeStatePage(url="https://www.site.com/x", title="验证中心", body="")
        self.assertEqual(wall_hit(cast("Page", page)), "验证中心")

    def test_body_form_with_extra_keys(self) -> None:
        page = _FakeStatePage(url="https://www.site.com/x", title="店名", body="请完成下方验证")
        self.assertEqual(wall_hit(cast("Page", page), ["请完成下方验证"]), "请完成下方验证")

    def test_clean_page_is_none(self) -> None:
        page = _FakeStatePage(
            url="https://www.site.com/list", title="【上海烧烤】", body="人均¥102 营业中",
        )
        self.assertIsNone(wall_hit(cast("Page", page)))


class TestWaitLoggedIn(unittest.TestCase):
    """wait_logged_in：手动登录等待，含"身份元素须刷新才出现"的关键行为。"""

    def test_identity_appears_only_after_reload(self) -> None:
        # 扫码成功后 cookie 已落但页面不刷新：身份元素只在 reload 后出现
        page = _FakeStatePage(identity_count=0)

        def on_reload() -> None:
            page.identity_count = 1

        page.on_reload = on_reload  # type: ignore[method-assign]
        ok = wait_logged_in(cast("Page", page), "a.member", timeout_s=5, reload_every_s=0, poll_s=0)
        self.assertTrue(ok)
        self.assertGreaterEqual(page.reloads, 1)

    def test_identity_present_immediately(self) -> None:
        page = _FakeStatePage(identity_count=1)
        self.assertTrue(wait_logged_in(cast("Page", page), "a.member", timeout_s=5, poll_s=0))
        self.assertEqual(page.reloads, 0)  # 检测先于刷新完成，不打扰用户

    def test_timeout_returns_false(self) -> None:
        page = _FakeStatePage(identity_count=0)
        self.assertFalse(wait_logged_in(cast("Page", page), "a.member", timeout_s=0.02, poll_s=0))


class TestWaitWallCleared(unittest.TestCase):
    """wait_wall_cleared：等用户手动过墙，墙消失即 True。"""

    def test_cleared_after_manual_pass(self) -> None:
        page = _FakeStatePage(url="https://verify.meituan.com/v2/app")
        original_wait = page.wait_for_timeout

        def wait(ms: int) -> None:
            # 第 2 次轮询前用户已过墙：302 回原地址
            page.url = "https://www.dianping.com/shanghai/ch10/g508r7?response_code=ok"
            original_wait(ms)

        page.wait_for_timeout = wait  # type: ignore[method-assign]
        self.assertTrue(wait_wall_cleared(cast("Page", page), timeout_s=5, poll_s=0))

    def test_still_walled_timeout(self) -> None:
        page = _FakeStatePage(url="https://verify.meituan.com/v2/app")
        self.assertFalse(wait_wall_cleared(cast("Page", page), timeout_s=0.02, poll_s=0))


# ---------- launch_browser_persistent / 回退链 ----------
_NO_AUTO_INSTALL = {"BAIBAO_BROWSER_AUTO_INSTALL": "false"}


class TestLaunchBrowserPersistent(unittest.TestCase):
    """launch_browser_persistent：低调默认值 + 与 launch_browser 同一条浏览器选择链。"""

    def test_builtin_success_lowprofile_defaults(self) -> None:
        chromium = _StubChromium()
        ctx = launch_browser_persistent(
            cast("Playwright", _StubPlaywright(chromium)), user_data_dir="site_profile",
        )
        self.assertEqual(ctx, ("ctx", "site_profile"))
        (kwargs,) = chromium.attempts
        self.assertEqual(kwargs["user_data_dir"], "site_profile")
        self.assertFalse(kwargs["headless"])                      # 默认有头
        self.assertEqual(kwargs["locale"], "zh-CN")
        self.assertNotIn("viewport", kwargs)                      # 默认不模拟视口（跟随真实窗口）
        self.assertNotIn("channel", kwargs)
        self.assertNotIn("executable_path", kwargs)
        self.assertIn("--disable-blink-features=AutomationControlled", kwargs["args"])

    def test_pathlib_user_data_dir_accepted(self) -> None:
        from pathlib import Path

        chromium = _StubChromium()
        launch_browser_persistent(
            cast("Playwright", _StubPlaywright(chromium)),
            user_data_dir=Path("prof") / "site",
        )
        self.assertEqual(chromium.attempts[0]["user_data_dir"], os.fspath(Path("prof") / "site"))

    def test_automation_flag_not_duplicated(self) -> None:
        chromium = _StubChromium()
        launch_browser_persistent(
            cast("Playwright", _StubPlaywright(chromium)),
            user_data_dir="p",
            args=["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
        )
        self.assertEqual(
            chromium.attempts[0]["args"],
            ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
        )

    def test_explicit_viewport_and_slow_mo_forwarded(self) -> None:
        chromium = _StubChromium()
        launch_browser_persistent(
            cast("Playwright", _StubPlaywright(chromium)), user_data_dir="p",
            viewport={"width": 1366, "height": 900}, slow_mo=50,
        )
        kwargs = chromium.attempts[0]
        self.assertEqual(kwargs["viewport"], {"width": 1366, "height": 900})
        self.assertEqual(kwargs["slow_mo"], 50)

    def test_fallback_to_local_channel_when_builtin_missing(self) -> None:
        chromium = _StubChromium(fail_builtins=1)
        with patch.dict(os.environ, _NO_AUTO_INSTALL):
            launch_browser_persistent(
                cast("Playwright", _StubPlaywright(chromium)), user_data_dir="p",
            )
        # 尝试序列：内置（失败）→ channel chrome（成功）；不再往下试 msedge
        self.assertEqual(
            [(a.get("channel"), a.get("executable_path")) for a in chromium.attempts],
            [(None, None), ("chrome", None)],
        )

    def test_fallback_skips_to_msedge_when_chrome_missing(self) -> None:
        chromium = _StubChromium(fail_builtins=1, fail_channels={"chrome"})
        with patch.dict(os.environ, _NO_AUTO_INSTALL):
            launch_browser_persistent(
                cast("Playwright", _StubPlaywright(chromium)), user_data_dir="p",
            )
        self.assertEqual(
            [a.get("channel") for a in chromium.attempts], [None, "chrome", "msedge"],
        )

    def test_explicit_chrome_path_used_before_channels(self) -> None:
        chromium = _StubChromium(fail_builtins=1)
        with patch.dict(os.environ, _NO_AUTO_INSTALL):
            launch_browser_persistent(
                cast("Playwright", _StubPlaywright(chromium)), user_data_dir="p",
                chrome_path=r"C:\chrome.exe",
            )
        self.assertEqual(chromium.attempts[1]["executable_path"], r"C:\chrome.exe")

    def test_force_builtin_reraises_when_missing(self) -> None:
        chromium = _StubChromium(fail_builtins=1)
        with (
            patch.dict(os.environ, _NO_AUTO_INSTALL),
            self.assertRaisesRegex(RuntimeError, "内置 Chromium 缺失"),
        ):
            launch_browser_persistent(
                cast("Playwright", _StubPlaywright(chromium)), user_data_dir="p",
                use_builtin_chromium=True,
            )


class TestLaunchBrowserChainRegression(unittest.TestCase):
    """launch_browser（重构后）：普通启动共用同一条选择链，行为不变。"""

    def test_builtin_success(self) -> None:
        chromium = _StubChromium()
        browser = launch_browser(cast("Playwright", _StubPlaywright(chromium)))
        self.assertEqual(browser, ("browser", None))
        (kwargs,) = chromium.attempts
        self.assertFalse(kwargs["headless"])
        self.assertNotIn("slow_mo", kwargs)

    def test_fallback_chain_same_as_persistent(self) -> None:
        chromium = _StubChromium(fail_builtins=1, fail_channels={"chrome"})
        with patch.dict(os.environ, _NO_AUTO_INSTALL):
            launch_browser(cast("Playwright", _StubPlaywright(chromium)))
        self.assertEqual(
            [a.get("channel") for a in chromium.attempts], [None, "chrome", "msedge"],
        )

    def test_headless_and_slow_mo_forwarded(self) -> None:
        chromium = _StubChromium()
        launch_browser(
            cast("Playwright", _StubPlaywright(chromium)), headless=True, slow_mo=30,
        )
        kwargs = chromium.attempts[0]
        self.assertTrue(kwargs["headless"])
        self.assertEqual(kwargs["slow_mo"], 30)


if __name__ == "__main__":
    unittest.main()

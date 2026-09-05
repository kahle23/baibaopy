"""
devtools — 浏览器开发工具协议（DevTools Protocol / CDP）薄封装。

底层真实输入与协议级能力的统一收口，覆盖 Playwright 全部内核（chromium / firefox / webkit）。上层（BasePage、probe）只调用 :func:`real_click` 等入口，不感知内核差异。

  - :func:`engine_name` — 当前页面的浏览器内核名。
  - :func:`real_click` — 真实鼠标点击，按内核自动分流：
      * Chromium 系（chrome/msedge/内置 Chromium）：CDP ``Input.dispatchMouseEvent`` 完整事件序列；
        （2026-08-28 IMP/WMS 生产实跑验证，el-select 在弹窗内展开/选中的唯一可靠方式）
      * Firefox/WebKit：CDP 不存在，走 Playwright ``page.mouse`` 的 move→down→up 原生序列；
        （同为 trusted 输入、无 actionability 检查，语义等价）按 Playwright 官方语义实现，尚未生产实跑——将来实跑若有差异，只需调整本文件。
  - :func:`new_session` — 创建 CDP 会话（网络拦截、协议级截图等 CDP 独有能力），仅 Chromium 系可用，其它内核抛出明确错误。

为什么需要"真实输入"：Element Plus el-select 在 el-dialog 内时，Playwright ``locator.click()`` 的合成事件会被 overlay 拦截。
无法触发 Vue 的 pointerdown 处理链；CDP/page.mouse 发送的是浏览器底层真实输入，才能完整触发。
本包 :func:`baibao.autotest.core.browser.launch_browser` 只启动 Chromium 系，因此默认路径始终走 CDP。
Firefox/WebKit 分支供使用方自建内核的 Page 复用同一套交互逻辑。

依赖：playwright（重依赖，按需懒加载，不在模块顶层导入）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.sync_api import CDPSession, Page

__all__ = ["engine_name", "new_session", "real_click"]


def engine_name(page: Page) -> str:
    """
    当前页面所属的 Playwright 浏览器内核名。

    Returns:
        ``"chromium"`` / ``"firefox"`` / ``"webkit"``。
        chrome/msedge 等 channel 也属于 chromium 内核。
        取不到（Page 未绑定 Browser，如测试 stub）时按本包默认场景返回 ``"chromium"``。
    """
    browser = page.context.browser
    if browser is None:
        return "chromium"
    return browser.browser_type.name


def new_session(page: Page) -> CDPSession:
    """
    创建 CDP 会话（DevTools Protocol 独有能力入口）。

    供网络拦截、协议级截图等仅协议层能做的操作使用，用完记得 ``detach()``。

    Raises:
        NotImplementedError: 当前内核不是 Chromium 系——Firefox/WebKit 没有 DevTools Protocol，请改用 Playwright 公共 API。
    """
    name = engine_name(page)
    if name != "chromium":
        raise NotImplementedError(
            f"CDP 会话仅 Chromium 系浏览器支持（当前内核：{name}）。"
            "Firefox/WebKit 无 DevTools Protocol，请改用 Playwright 公共 API。",
        )
    return page.context.new_cdp_session(page)


def real_click(page: Page, x: float, y: float) -> None:
    """
    在视口坐标 ``(x, y)`` 发送真实鼠标点击（完整按下-释放序列）。

    解决 Element Plus el-select 在弹窗内 Playwright 合成事件无法触发展开/ 选中的问题（Vue 3 + Element Plus 已知兼容性）。
    按内核自动分流，见模块 docstring；调用方无需关心当前浏览器。
    """
    if engine_name(page) == "chromium":
        _click_via_cdp(page, x, y)
    else:
        _click_via_mouse(page, x, y)


def _click_via_cdp(page: Page, x: float, y: float) -> None:
    """
    Chromium 系：CDP ``Input.dispatchMouseEvent`` 完整事件序列。

    完整序列：mouseMoved → mousePressed → mouseReleased（浏览器底层真实输入，正确触发 Vue 的 pointerdown/mousedown 处理链）。
    """
    cdp = new_session(page)
    # playwright 的 CDPSession.send 签名含裸 Dict（strict 下视为 Unknown），收口为具型回调
    send = cast(Callable[[str, dict[str, Any]], Any], getattr(cdp, "send"))  # noqa: B009
    try:
        send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })
        time.sleep(0.05)
        send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        time.sleep(0.03)
        send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
    finally:
        cdp.detach()


def _click_via_mouse(page: Page, x: float, y: float) -> None:
    """
    Firefox/WebKit：``page.mouse`` 原生输入序列（等价语义）。

    与 CDP 路径同为 trusted 输入、无 actionability 检查；间隔与 CDP 路径保持一致（move 后 50ms、按下后 30ms）。
    """
    page.mouse.move(x, y)
    time.sleep(0.05)
    page.mouse.down()
    time.sleep(0.03)
    page.mouse.up()

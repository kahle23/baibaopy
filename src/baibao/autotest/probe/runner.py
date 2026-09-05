"""
runner — DOM 摘要探针的编排层。

把一个**已登录**页面压缩成 KB 级 markdown 结构摘要（表单项/表格/按钮/弹窗/ 消息/下拉实际选项），供 AI 在不读整页 HTML 的前提下了解页面结构：

  - :func:`run_probe` — 完整流程入口（登录态缓存 + 浏览器 + 打开页面 + 提取），CLI 与脚本两用。
  - :func:`extract_summary` — 在已打开的 ``Page`` 上注入 JS 提取并格式化摘要。

提取脚本见 :mod:`.extract_js`；渲染见 :mod:`.render`；地址规范化见 :mod:`.url`。
选择器知识沿用 :mod:`baibao.autotest.page`（BasePage）的 Element Plus 约定；点击交互复用真实输入事件（el-select 展开的唯一可靠方式）。

依赖：playwright（重依赖，按需懒加载，不在模块顶层导入）。
安装：``pip install "baibao[autotest]"``。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..core.browser import launch_browser
from ..core.devtools import real_click
from ..core.login_state import (
    LoginCfg,
    auth_state_path,
    is_auth_valid,
    save_storage_state,
)
from .extract_js import EXTRACT_JS
from .render import format_custom, format_summary
from .url import build_target_url

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

__all__ = ["ProbeOptions", "extract_summary", "run_probe", "run_probe_with"]


@dataclass
class ProbeOptions:
    """
    探针完整参数集（run_probe 16 参的对象化封装，规范 2.3）。

    Attributes:
        target: hash 路由（如 ``#/oa/asset``）或完整 URL。
        base_url: 被测系统基础地址（``target`` 非 http 时拼接）。
        account: 账号名——登录态槽位（缓存文件名 ``<auth_dir>/<account>.json``），
            非系统权限角色。
        auth_dir: 登录态缓存目录。
        username / password / captcha: 登录凭据（缓存有效时不使用）。
        login_cfg: 登录流程配置，默认 LoginCfg（若依/RuoYi 风格）。
        click_labels: 提取前依次点击的表单项标签（展开下拉拿实际选项）。
        headless: 是否无头模式，默认 False（有头）。
        use_builtin_chromium: None 自动 / True 强制内置 / False 跳过内置。
        chrome_path: 本地 Chrome 路径；None 时按 CHROME_PATH 环境变量探测。
        slow_mo: 慢放延迟毫秒（调试用）。
        settle_ms: 页面就绪后的额外稳定等待毫秒。
        brief: 超简略模式（只留页面骨架）。
        extract_js: 自定义提取 JS，返回其结果的紧凑 JSON 替代内置摘要。
    """

    target: str
    base_url: str
    account: str = "admin"
    auth_dir: Path | str = ".auth"
    username: str = ""
    password: str = ""
    captcha: str = ""
    login_cfg: LoginCfg | None = None
    click_labels: Sequence[str] = ()
    headless: bool = False
    use_builtin_chromium: bool | None = None
    chrome_path: str | None = None
    slow_mo: int = 0
    settle_ms: int = 600
    brief: bool = False
    extract_js: str | None = None


def extract_summary(page: Page, *, brief: bool = False) -> str:
    """
    在已打开的 ``Page`` 上注入 :data:`EXTRACT_JS` 并返回 markdown 摘要。
    """
    data = cast("dict[str, Any]", page.evaluate(EXTRACT_JS))
    return format_summary(data, brief=brief)


def _click_label(page: Page, label: str) -> None:
    """
    按标签文本点击表单控件（优先 el-select wrapper 的真实点击，用于展开下拉）。

    找不到 el-select 时退化为按文本点击可见按钮（触发弹窗/页签等交互后提取）。
    """
    dialog = page.locator(".el-dialog").first
    base = dialog if dialog.is_visible() else page
    wrapper = (
        base.locator(".el-form-item", has_text=label).first
        .locator(".el-select__wrapper").first
    )
    if wrapper.count() and wrapper.is_visible():
        box = wrapper.bounding_box()
        if box:
            real_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.wait_for_timeout(400)
            return
    btn = page.get_by_role("button", name=label).first
    if btn.count() and btn.is_visible():
        try:
            btn.click(timeout=3000)
        except Exception:
            btn.evaluate("el => el.click()")
        page.wait_for_timeout(400)
        return
    raise RuntimeError(f"找不到可点击的表单项/按钮：{label}")


def _resolve_state_file(
    browser: Browser, cfg: LoginCfg, auth_dir: Path, account: str,
    base_url: str, username: str, password: str, captcha: str,
) -> Path:
    """
    登录态缓存校验与必要时的重登，返回可用的 storage_state 文件路径。

    身份绑定校验：缓存属另一账号/站点时强制重登（防跨账号串用得出错误视角的结论）；缓存失效且缺凭据时报错。
    """
    state_file = auth_state_path(auth_dir, account)
    if not is_auth_valid(
        state_file, username=username or None, base_url=base_url or None,
    ):
        if not (username and password):
            raise RuntimeError(
                f"登录态缓存无效（{state_file}）且未提供账号密码"
                f"（account={account}）：请传 username/password 或配置"
                " ADMIN_USERNAME/ADMIN_PASSWORD 环境变量",
            )
        state_file = Path(save_storage_state(
            browser, cfg, auth_dir, account,
            base_url, username, password, captcha,
        ))
    return state_file


def _open_page(context: BrowserContext, target: str, base_url: str, settle_ms: int) -> Page:
    """
    在 context 中深链打开目标页并等待就绪（SPA 动态路由 404 自动重试）。

    动态路由 SPA（如若依）在菜单未注册完时会跳 404：检测到 404 则等待后重新导航，最多 3 轮。长轮询页面到不了 networkidle，接受现状。
    """
    page = context.new_page()
    page.set_default_timeout(15000)
    page.set_default_navigation_timeout(30000)

    url = build_target_url(target, base_url)
    page.goto(url)
    for _ in range(3):
        page.wait_for_load_state("domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # 长轮询页面到不了 networkidle，接受现状
        if "/404" not in page.url:
            break
        page.wait_for_timeout(800)
        page.goto(url)
    page.wait_for_timeout(settle_ms)
    return page


def run_probe(
    target: str,
    *,
    base_url: str,
    account: str = "admin",
    auth_dir: Path | str = ".auth",
    username: str = "",
    password: str = "",
    captcha: str = "",
    login_cfg: LoginCfg | None = None,
    click_labels: Sequence[str] = (),
    headless: bool = False,
    use_builtin_chromium: bool | None = None,
    chrome_path: str | None = None,
    slow_mo: int = 0,
    settle_ms: int = 600,
    brief: bool = False,
    extract_js: str | None = None,
) -> str:
    """
    打开已登录页面并返回 DOM 摘要（markdown）。

    完整流程：登录态缓存（失效且给了账号密码则自动重登）→ 打开页面 → 依次点击 ``click_labels`` → 注入 JS 提取 → 返回摘要字符串。

    旧签名为兼容外部调用方（CLI、browser-e2e-runner 技能等）保留；
    编程调用推荐改用 :class:`ProbeOptions` + :func:`run_probe_with`。

    Args:
        target: hash 路由（如 ``#/oa/asset``）或完整 URL。
        base_url: 被测系统基础地址（``target`` 非 http 时拼接）。
        account: 账号名——登录态槽位（缓存文件名 ``<auth_dir>/<account>.json``），
            非系统权限角色。
        auth_dir: 登录态缓存目录。
        username / password / captcha: 登录凭据（缓存有效时不使用；
            缓存失效且缺凭据时报错）。
        login_cfg: 登录流程配置，默认 :class:`LoginCfg`（若依/RuoYi 风格）。
        click_labels: 提取前依次点击的表单项标签（展开下拉拿实际选项）。
        headless: 是否无头模式，默认 **False（有头）**——用户不强调一律有头。
        use_builtin_chromium: ``None``（默认）自动模式（内置 Chromium 优先，缺失自动下载，失败回退本地 Chrome → Edge）；``True`` 强制内置；
            ``False`` 跳过内置走本地链。``USE_BUILTIN_CHROMIUM`` 环境变量（true/false）可覆盖默认。
        chrome_path: 本地 Chrome 路径；为 ``None`` 时按 ``CHROME_PATH`` 环境变量探测。
        slow_mo: 慢放延迟毫秒（调试用）。
        settle_ms: 页面就绪后的额外稳定等待毫秒。
        brief: 超简略模式（只留页面骨架，见 :func:`format_summary`）。
        extract_js: 自定义提取 JS（表达式或箭头函数均可）——在页面上执行并返回其结果的紧凑 JSON，**替代**内置摘要。配合 ``click_labels`` 可先交互再取任意局部数据（如整列值、聚合统计）。

    Returns:
        markdown 摘要字符串（``extract_js`` 时为紧凑 JSON 字符串）。
    """
    return run_probe_with(ProbeOptions(
        target=target,
        base_url=base_url,
        account=account,
        auth_dir=auth_dir,
        username=username,
        password=password,
        captcha=captcha,
        login_cfg=login_cfg,
        click_labels=click_labels,
        headless=headless,
        use_builtin_chromium=use_builtin_chromium,
        chrome_path=chrome_path,
        slow_mo=slow_mo,
        settle_ms=settle_ms,
        brief=brief,
        extract_js=extract_js,
    ))


def run_probe_with(opts: ProbeOptions) -> str:
    """
    :func:`run_probe` 的参数对象版实现入口。

    流程与异常语义与 :func:`run_probe` 完全一致，参数见 :class:`ProbeOptions`。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError(
            '探针依赖 playwright，请先安装：pip install "baibao[autotest]"',
        ) from exc

    cfg = opts.login_cfg or LoginCfg()
    auth_dir_path = Path(opts.auth_dir)

    # 内置/本地选择三态：参数显式值 > USE_BUILTIN_CHROMIUM 环境变量 > 自动模式
    use_builtin = opts.use_builtin_chromium
    if use_builtin is None:
        env_val = os.getenv("USE_BUILTIN_CHROMIUM", "").lower()
        use_builtin = (
            True if env_val == "true" else (False if env_val == "false" else None)
        )
    # chrome_path 仅显式传入/环境变量时用；本地链的自动探测交给 launch_browser
    detected = (
        opts.chrome_path if opts.chrome_path is not None
        else (os.getenv("CHROME_PATH") or None)
    )

    with sync_playwright() as p:
        browser = launch_browser(
            p, headless=opts.headless, slow_mo=opts.slow_mo,
            use_builtin_chromium=use_builtin, chrome_path=detected,
        )
        try:
            state_file = _resolve_state_file(
                browser, cfg, auth_dir_path, opts.account,
                opts.base_url, opts.username, opts.password, opts.captcha,
            )
            context = browser.new_context(
                storage_state=str(state_file), viewport=cfg.viewport,  # type: ignore[arg-type]
            )
            try:
                page = _open_page(context, opts.target, opts.base_url, opts.settle_ms)

                for label in opts.click_labels:
                    _click_label(page, label)

                if opts.extract_js is not None:
                    return format_custom(page.evaluate(opts.extract_js))
                return extract_summary(page, brief=opts.brief)
            finally:
                context.close()
        finally:
            browser.close()

"""
conftest_template — 角色级 fixture 参考样板（**复制用模板，勿 import**）。

**本文件不是可直接生效的 conftest**，而是参考样板。请**复制本文件内容** 到你的项目根目录命名为 ``conftest.py``，然后按你的被测系统与角色改造。
（历史上本文件被误 import 时会在 import 处创建 ``.auth`` 目录，现已去除全部模块级副作用，但复制改造仍是唯一正确用法。）

展示了如何基于 :mod:`baibao.autotest` 的基础设施组装角色级 fixture。
（``fixtures`` 提供的 ``browser``/``base_url`` 等 + ``core.login_state`` 的 ``LoginCfg``/``save_storage_state``）

  - ``admin_storage_state`` (session) — 管理员登录态缓存
  - ``admin_page`` (function) — 每个用例独立 context + page（带登录态）
  - ``context`` / ``api_context`` (function) — 复用登录态 cookie 的 API 上下文

前置：在项目根 conftest.py 顶部启用基础 fixture::

    pytest_plugins = ["baibao.autotest.fixtures"]

下面是角色级 fixture 示例（复制到 conftest.py 后改造）。
"""

# ↓↓↓ 复制以下内容到项目根 conftest.py 后改造 ↓↓↓

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import (
    APIRequestContext,
    Browser,
    BrowserContext,
    Page,
    ViewportSize,
)

from baibao.autotest.core.login_state import (
    LoginCfg,
    is_auth_valid,
    save_storage_state,
)

# ---------------------------------------------------------------------------
# 登录配置（按你的后台改造字段选择器；默认值匹配若依/RuoYi 风格）
# ---------------------------------------------------------------------------

LOGIN_CFG = LoginCfg(
    # login_url_suffix="/#/login",          # hash 路由；非 hash 路由用 "/login"
    # username_selector='input[placeholder="用户名"]',
    # password_selector='input[type="password"]',
    # login_button_selector='button.el-button--primary',
    # captcha_selectors=[...],              # 无验证码置为 []
    # success_url_not_contains="/login",
    # viewport={"width": 1920, "height": 1080},
)

# 登录态缓存目录（无需预先创建：save_storage_state 会自动 mkdir）
AUTH_DIR = Path(__file__).resolve().parent / ".auth"


def _cfg(key: str, default: str = "") -> str:
    """
    读取环境变量配置（登录凭据等）。
    """
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# 管理员登录态（session 级复用，首次登录后缓存 12h）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def admin_storage_state(browser: Browser, base_url: str) -> str:
    """
    管理员登录态。首次登录后缓存到 ``.auth/admin.json``，12 小时内免登录。
    """
    from baibao.autotest.core.login_state import auth_state_path

    state_file = auth_state_path(AUTH_DIR, "admin")
    if is_auth_valid(state_file):
        return str(state_file)

    return save_storage_state(
        browser, LOGIN_CFG, AUTH_DIR, "admin", base_url,
        _cfg("ADMIN_USERNAME"), _cfg("ADMIN_PASSWORD"),
        _cfg("CAPTCHA_VALUE", ""),
    )


# ---------------------------------------------------------------------------
# 用例级 fixture（每个用例独立 context + page，带登录态）
# ---------------------------------------------------------------------------

@pytest.fixture()
def admin_page(browser: Browser, admin_storage_state: str) -> Generator[Page, None, None]:
    """
    已登录管理员的标准页面。

    每个用例独立 context（带登录态），互不污染。用法::

        def test_xxx(admin_page):
            admin_page.goto(...) ...
    """
    context = browser.new_context(
        storage_state=admin_storage_state,
        viewport=cast("ViewportSize | None", LOGIN_CFG.viewport),
    )
    page = context.new_page()
    page.set_default_timeout(15000)
    page.set_default_navigation_timeout(30000)
    yield page
    page.close()
    context.close()


@pytest.fixture()
def context(browser: Browser, admin_storage_state: str) -> Generator[BrowserContext, None, None]:
    """
    已登录管理员的浏览器上下文（供 ``api_context`` 等使用）。
    """
    ctx = browser.new_context(
        storage_state=admin_storage_state,
        viewport=cast("ViewportSize | None", LOGIN_CFG.viewport),
    )
    yield ctx
    ctx.close()


@pytest.fixture()
def api_context(context: BrowserContext) -> APIRequestContext:
    """
    API 请求上下文（复用登录态 cookie）。供 :class:`baibao.autotest.api.ApiBase` 子类使用。
    """
    return context.request


# ---------------------------------------------------------------------------
# 页面 URL fixture 示例（按你的路由改造）
# ---------------------------------------------------------------------------

@pytest.fixture()
def asset_management_url(base_url: str) -> str:
    """
    资产管理页面 URL（hash 路由示例）。
    """
    return f"{base_url}/#/it-asset"


# ---------------------------------------------------------------------------
# 用例标记（按你的模块改造）
# ---------------------------------------------------------------------------

def pytest_configure(config: Any) -> None:
    """
    注册业务测试标记（smoke/crud/flow/form/query）。
    """
    for marker in ["smoke", "crud", "flow", "form", "query"]:
        config.addinivalue_line("markers", f"{marker}: 业务测试标记")


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """
    按测试文件名自动分配模块标记。
    """
    for item in items:
        if "test_crud" in item.nodeid:
            item.add_marker(pytest.mark.crud)
        elif "test_flow" in item.nodeid:
            item.add_marker(pytest.mark.flow)

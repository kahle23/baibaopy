"""
fixtures — 可选启用的 pytest fixture（自管理 Playwright 模式）。

启用方式：在项目根 ``conftest.py`` 加一行::

    pytest_plugins = ["baibao.autotest.fixtures"]

提供的 fixture：

  - ``base_url`` / ``is_headless`` / ``slow_mo`` (session) — 环境变量驱动
  - ``playwright`` / ``browser`` (session) — 自管理 Playwright 生命周期
  - ``today_str`` / ``faker`` / ``unique_id`` (function) — 通用测试数据

**不**提供角色级 fixture（``storage_state`` / ``page`` / ``api_context``），它们与具体角色和登录流程相关。
请在项目 conftest 中基于 :mod:`baibao.autotest.core.login_state` 自行实例化，参考 :mod:`baibao.autotest.fixtures.conftest_template`。

刻意**不**使用 pytest-playwright 的 ``context`` / ``page`` fixture。
原因：Element Plus el-select 在弹窗内的事件兼容问题（pytest-playwright 托管的 context 无法可靠触发 Vue pointerdown）。

依赖：需安装 ``baibao[autotest]``（playwright / python-dotenv / faker）。
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from ..core.envutil import load_dotenv_if_present, normalize_base_url

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright

# 可选加载项目根 .env（python-dotenv 未安装则跳过，直接读 os.environ）
load_dotenv_if_present()


def _cfg(key: str, default: str = "") -> str:
    """
    读取环境变量配置。
    """
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# 基础配置 fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url() -> str:
    """
    被测系统基础地址（环境变量 ``BASE_URL``）。
    """
    return normalize_base_url(_cfg("BASE_URL", "http://localhost:8080"))


@pytest.fixture(scope="session")
def is_headless() -> bool:
    """
    是否无头模式（环境变量 ``HEADLESS``，默认 false）。
    """
    return _cfg("HEADLESS", "false").lower() == "true"


@pytest.fixture(scope="session")
def slow_mo() -> int:
    """
    慢放延迟毫秒（环境变量 ``SLOW_MO``，默认 0）。
    """
    try:
        return int(_cfg("SLOW_MO", "0"))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Playwright 生命周期（session 级，自管理）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def playwright() -> Generator[Playwright, None, None]:
    """
    session 级启动 Playwright。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright: Playwright, is_headless: bool, slow_mo: int) -> Generator[Browser, None, None]:
    """
    session 级启动浏览器。

    环境变量 ``USE_BUILTIN_CHROMIUM`` 三态：

      - 未设置（默认）→ 自动模式：内置 Chromium 优先，缺失自动下载（镜像优先、官方回退），下载失败回退本地 Chrome → Edge
      - ``true``  → 强制内置 Chromium（缺失自动下载，失败抛错）
      - ``false`` → 跳过内置，直接本地浏览器（``CHROME_PATH`` 显式路径优先）
    """
    from ..core.browser import launch_browser

    raw = _cfg("USE_BUILTIN_CHROMIUM")
    if raw == "":
        use_builtin = None
    else:
        use_builtin = raw.lower() == "true"
    browser = launch_browser(
        playwright,
        headless=is_headless,
        slow_mo=slow_mo,
        use_builtin_chromium=use_builtin,
        chrome_path=_cfg("CHROME_PATH") or None,
    )
    yield browser
    browser.close()


# ---------------------------------------------------------------------------
# 测试数据辅助 fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def today_str() -> str:
    """
    今天的 ISO 日期字符串（``YYYY-MM-DD``）。
    """
    return date.today().isoformat()


@pytest.fixture()
def unique_id() -> str:
    """
    唯一 ID：``TEST-<时间戳>``。需自定义前缀请在项目 conftest 自建 fixture。
    """
    return f"TEST-{int(time.time())}"


@pytest.fixture()
def faker() -> Any:
    """
    ``Faker("zh_CN")`` 实例（中文假数据生成）。

    依赖 ``faker`` 包（已含在 ``baibao[autotest]`` extra）。
    pip 装 faker 在国内默认镜像可能失败，建议用清华源：
    ``pip install faker -i https://pypi.tuna.tsinghua.edu.cn/simple/``
    """
    from faker import Faker

    return Faker("zh_CN")

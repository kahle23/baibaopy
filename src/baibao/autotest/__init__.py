"""
autotest — Playwright E2E 测试基础设施。

把日常 Web 自动化测试中反复用到的能力封装成简洁 API：页面对象基类、浏览器启动、登录态缓存、接口基类、可选启用的 pytest fixture。

核心特性：

  - :class:`BasePage` — Element Plus 组件操作 + 真实输入 + 对话框作用域
  - :class:`LoginCfg` + :func:`do_login` — 数据驱动的登录流程
  - :func:`save_storage_state` / :func:`is_auth_valid` — 登录态缓存（TTL）
  - :class:`ApiBase` — 复用浏览器登录态的后端接口基类
  - :func:`detect_chrome_path` / :func:`launch_browser` — 本地 Chrome 探测与启动
  - :func:`launch_browser_persistent` / :func:`human_pause` / :func:`wall_hit` /
    :func:`wait_logged_in` / :func:`wait_wall_cleared` —
    风控敏感站点"低调运行"（持久化上下文 + 随机节奏 + 验证墙检测/即停 + 手动登录/过墙等待）
  - :func:`run_probe` / :func:`extract_summary` — DOM 摘要探针（页面压缩成 KB 级 markdown）
  - :mod:`fixtures` — opt-in pytest fixture（``pytest_plugins`` 启用）

分包结构（旧扁平模块路径由根目录 shim 继续保底）::

    core/     浏览器、登录态、真实输入(devtools)、轮询(polling)、环境工具(envutil)、低调运行小件(lowprofile)
    page/     BasePage 页面对象基类
    api/      ApiBase 接口基类
    probe/    DOM 摘要探针（extract_js / url / render / runner）
    fixtures/ opt-in pytest fixture + conftest 样板

按需从子模块导入，例如::

    from baibao.autotest.page import BasePage
    from baibao.autotest.api import ApiBase
    from baibao.autotest.core.login_state import LoginCfg, save_storage_state

依赖：playwright 为重依赖，按需懒加载，不在模块顶层导入。
安装：``pip install "baibao[autotest]"``。
"""

from . import (
    api,
    browser,
    dom_summary,
    login_state,
    lowprofile,
    page,
)
from .api import ApiBase
from .core.browser import detect_chrome_path, launch_browser, launch_browser_persistent
from .core.login_state import (
    LoginCfg,
    auth_state_path,
    do_login,
    is_auth_valid,
    save_storage_state,
)
from .core.lowprofile import (
    human_pause,
    risk_wall_hit,
    wait_logged_in,
    wait_wall_cleared,
    wall_hit,
)
from .page import BasePage
from .probe import extract_summary, format_summary, run_probe

__all__ = [
    "ApiBase",
    "BasePage",
    "LoginCfg",
    "api",
    "auth_state_path",
    "browser",
    "detect_chrome_path",
    "do_login",
    "dom_summary",
    "extract_summary",
    "format_summary",
    "human_pause",
    "is_auth_valid",
    "launch_browser",
    "launch_browser_persistent",
    "login_state",
    "lowprofile",
    "page",
    "risk_wall_hit",
    "run_probe",
    "save_storage_state",
    "wait_logged_in",
    "wait_wall_cleared",
    "wall_hit",
]

"""
core — autotest 的基础设施层：浏览器、登录态与共享机制。

只被上层（page / api / probe / fixtures）依赖，自身不依赖任何上层模块。
共享机制：devtools（真实输入收口）、polling（通用轮询）、envutil（环境工具）。
"""

from .browser import detect_chrome_path, launch_browser, launch_browser_persistent
from .devtools import engine_name, new_session, real_click
from .envutil import load_dotenv_if_present, normalize_base_url
from .login_state import (
    LoginCfg,
    auth_state_path,
    do_login,
    is_auth_valid,
    save_storage_state,
)
from .lowprofile import (
    human_pause,
    risk_wall_hit,
    wait_logged_in,
    wait_wall_cleared,
    wall_hit,
)
from .polling import poll_until

__all__ = [
    "LoginCfg",
    "auth_state_path",
    "detect_chrome_path",
    "do_login",
    "engine_name",
    "human_pause",
    "is_auth_valid",
    "launch_browser",
    "launch_browser_persistent",
    "load_dotenv_if_present",
    "new_session",
    "normalize_base_url",
    "poll_until",
    "real_click",
    "risk_wall_hit",
    "save_storage_state",
    "wait_logged_in",
    "wait_wall_cleared",
    "wall_hit",
]

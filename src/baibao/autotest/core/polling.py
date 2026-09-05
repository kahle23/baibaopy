"""
polling — 通用轮询等待。

把"每隔 interval 检查一次条件，直到成立或超时"的重复模式收敛成一个 :func:`poll_until`。
此前 page.py 里同样的手写循环出现了 7 处（异步列表刷新、字典下拉异步渲染、loading 遮罩消失、远程搜索等场景）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

__all__ = ["poll_until"]


def poll_until(
    fn: Callable[[], Any],
    *,
    timeout_ms: int,
    interval_ms: int = 400,
    sleep_ms: Callable[[int], None] | None = None,
) -> Any:
    """
    反复调用 ``fn`` 直到返回真值或超时。

    超时**不抛错**——各场景的错误上下文不同（哪行哪列、哪个选择器），由调用方检查返回值后自行抛带上下文的异常。``fn`` 抛出的异常按原样向上传播（需要容错的场景在 ``fn`` 内自行 try/except）。

    Args:
        fn: 条件函数，返回真值表示满足（注意：``""`` / ``0`` 等空值视为未满足，断言空字符串的场景请让 ``fn`` 返回布尔值）。
        timeout_ms: 总超时毫秒（至少执行一轮 ``fn``）。
        interval_ms: 轮询间隔毫秒。
        sleep_ms: 等待函数（入参毫秒）。默认 ``time.sleep``；Playwright 场景传 ``page.wait_for_timeout``，等待期间可处理协议消息。

    Returns:
        ``fn`` 的最后返回值：满足时为真值；超时时为最后一代结果（通常为 None）。
    """
    if sleep_ms is None:
        def _default_sleep(ms: int) -> None:
            """
            默认等待：秒级 sleep（Playwright 场景请传 page.wait_for_timeout）。
            """
            time.sleep(ms / 1000)
        sleep_ms = _default_sleep
    deadline = time.monotonic() + timeout_ms / 1000
    result: Any = None
    while True:
        result = fn()
        if result:
            return result
        if time.monotonic() >= deadline:
            return result
        sleep_ms(interval_ms)

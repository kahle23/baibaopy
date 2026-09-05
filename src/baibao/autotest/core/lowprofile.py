"""
lowprofile — 风控敏感站点"低调运行"小件：随机节奏 + 验证墙检测/等待 + 手动登录等待。

定位：让自动化行为**像真人**（随机等待、触发风控立即停、人机校验留给用户手动过），
降低风控触发**概率**；不是也不做任何"绕过/破解"人机校验的事——验证码/滑块一律由
用户手动通过。

与 :func:`baibao.autotest.core.browser.launch_browser_persistent`（持久化上下文 + 有头）
配套构成完整低调运行姿势。
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

__all__ = [
    "human_pause",
    "risk_wall_hit",
    "wait_logged_in",
    "wait_wall_cleared",
    "wall_hit",
]

# 验证墙/风控拦截页的常见特征词。保守取舍：误命中只是多停一次（可恢复），
# 漏命中则会顶着验证墙继续请求（才是真伤害），故偏"宁可误停"。
# 站点特有措辞通过 extra_keys 补充，不改这里。
_RISK_WALL_KEYS: tuple[str, ...] = (
    "请依次点击",
    "请点击",
    "请拖动",
    "拖动滑块",
    "滑动验证",
    "点击验证",
    "安全验证",
    "验证中心",
    "异常请求",
    "访问受限",
    "访问频率",
)

# 整页 302 劫持型验证墙的 URL 特征（verify.xxx / captcha 域或路径）。
# 实测（2026-09）：连原页面都会被劫持到独立验证域，此时正文/登录元素全无——
# URL 是比正文更早更稳的信号，传入 risk_wall_hit 的 url 参数即优先检查。
_RISK_WALL_URL_KEYS: tuple[str, ...] = ("verify.", "captcha")

_BODY_HEAD_SIZE = 500


def human_pause(page: Page, lo: float = 3.0, hi: float = 8.0) -> float:
    """
    随机抖动等待（秒），返回实际暂停时长。

    匀速固定间隔（每页恰好 3s、每条恰好 2.5s 这类）是风控最容易识别的机器节奏
    特征；页与页、条目与条目之间用它替代固定 sleep，节奏自然像真人浏览。

    Args:
        page: Playwright ``Page``（用其 ``wait_for_timeout``，随 context 生命周期）。
        lo: 随机下界（秒），默认 3.0。
        hi: 随机上界（秒），默认 8.0。

    Returns:
        实际暂停的秒数（调用方可打日志对账）。
    """
    sec = random.uniform(lo, hi)
    page.wait_for_timeout(int(sec * 1000))
    return sec


def risk_wall_hit(
    text: str,
    extra_keys: Sequence[str] = (),
    *,
    url: str | None = None,
) -> str | None:
    """
    检测页面是否命中验证墙/风控拦截特征，命中返回关键词，未命中返回 ``None``。

    用途是"**触发即停**"的判定信号：抓取循环里每拿到一页就扫一遍（文本建议
    "标题 + 正文头部"，验证墙通常整页替换内容；或直接用 :func:`wall_hit` 传 Page），
    命中立即中止本次运行并保留已抓部分——顶着验证墙重试硬闯只会让风控升级
    （IP 限制 → 账号限制）。

    Args:
        text: 页面文本片段（建议"标题 + 正文头部"）。
        extra_keys: 站点特有特征词（追加在默认词表之后）。
        url: 页面当前 URL（可选）——整页 302 劫持型验证墙（verify.xxx/captcha 域）
            的最早最稳信号，传入即先查 URL 再查文本。

    Returns:
        命中的特征词/URL 特征（第一个匹配），未命中返回 ``None``。
    """
    if url:
        for key in _RISK_WALL_URL_KEYS:
            if key in url:
                return key
    if not text:
        return None
    for key in (*_RISK_WALL_KEYS, *extra_keys):
        if key and key in text:
            return key
    return None


def wall_hit(page: Page, extra_keys: Sequence[str] = ()) -> str | None:
    """
    页面级验证墙检测（:func:`risk_wall_hit` 的 Page 便捷版）。

    三层信号任一命中即返回关键词：① ``page.url`` 域名特征（verify./captcha 整页
    劫持，最早最稳）；② 页面 title + ③ 正文头部特征词。全部读取都吞异常——
    检测代码自身不能把任务带崩。
    """
    url = title = body_head = ""
    try:
        url = page.url
    except Exception:
        pass
    try:
        title = page.title()
    except Exception:
        pass
    try:
        body_head = page.inner_text("body")[:_BODY_HEAD_SIZE]
    except Exception:
        pass
    return risk_wall_hit(f"{title} {body_head}", extra_keys=extra_keys, url=url or None)


def wait_logged_in(
    page: Page,
    identity_selector: str,
    timeout_s: float = 240.0,
    *,
    reload_every_s: float = 20.0,
    poll_s: float = 3.0,
    verbose: bool = False,
) -> bool:
    """
    等待用户在可见窗口里完成手动登录（``identity_selector`` 出现即成功）。

    关键细节：用户登录成功后 cookie 已落，但页面**不会自动刷新**——身份元素
    （头像/会员链接）要等一次刷新才会出现，本函数周期性轻刷新解决这一点，
    没有它轮询会永远等下去。代价是刷新会关掉用户正在进行的登录弹窗（重新
    点开即可）；多数站点登录成功后页头会 ajax 自更新，检测先于首次刷新完成，
    不受影响。间隔可按站点行为调大。

    Args:
        page: 有头窗口的 Page（用户正在这个窗口里操作）。
        identity_selector: 登录后才有的身份元素选择器（头像/会员链接）——注意有的
            站点该元素只在部分页面类型渲染，轮询页要选对。
        timeout_s: 总超时（秒），默认 240。
        reload_every_s: 轻刷新间隔（秒），默认 20。
        poll_s: 轮询间隔（秒），默认 3。
        verbose: 打印等待进度（每 ~15s 一条）。

    Returns:
        登录成功 True；超时 False。
    """
    start = time.monotonic()
    last_reload = start
    next_progress = start + 15.0
    while True:
        now = time.monotonic()
        if now - start >= timeout_s:
            return False
        try:
            if page.locator(identity_selector).count() > 0:
                return True
        except Exception:
            pass
        if now - last_reload >= reload_every_s:
            try:
                page.reload(wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass
            last_reload = now
        if verbose and now >= next_progress:
            print(f"[{int(now - start)}s] 等待登录中...", flush=True)
            next_progress = now + 15.0
        page.wait_for_timeout(int(poll_s * 1000))


def wait_wall_cleared(
    page: Page,
    extra_keys: Sequence[str] = (),
    timeout_s: float = 180.0,
    *,
    poll_s: float = 3.0,
    verbose: bool = False,
) -> bool:
    """
    等待用户在可见窗口里手动通过验证墙（:func:`wall_hit` 变 ``None`` 即通过）。

    配合持久化上下文（同一 ``user_data_dir``）使用：手动过一次通常全站通行一段
    时间；通过后页面常已被 302 回原地址，调用方可直接继续操作。

    Args:
        page: 有头窗口的 Page（先 goto 被墙的 URL 再调本函数）。
        extra_keys: 站点特有验证墙特征词（透传 :func:`wall_hit`）。
        timeout_s: 总超时（秒），默认 180。
        poll_s: 轮询间隔（秒），默认 3。
        verbose: 每次轮询打印当前命中的墙特征。

    Returns:
        墙消失 True；超时 False。
    """
    start = time.monotonic()
    while True:
        now = time.monotonic()
        if now - start >= timeout_s:
            return False
        hit = wall_hit(page, extra_keys)
        if hit is None:
            return True
        if verbose:
            print(f"[{int(now - start)}s] 仍在验证页, 特征: {hit}", flush=True)
        page.wait_for_timeout(int(poll_s * 1000))

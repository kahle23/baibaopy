"""
autotest 命令 - Web 自动化测试（Playwright）辅助工具。

子命令：
- probe: DOM 摘要探针——打开已登录页面，输出 KB 级 markdown 结构摘要，
  供 AI 在不读整页 HTML 的前提下了解页面结构（省 token 的关键工具）。

规划中（未实现，勿依赖）：
- login: 预热/刷新指定账号的登录态缓存
- doctor: 环境自检（chrome / playwright / .env 配置）
"""

import argparse
import os
from pathlib import Path
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.autotest.core.envutil import load_dotenv_if_present, normalize_base_url

log = logutil.getLogger(__name__)


def _read_text_source(path: str | None) -> str | None:
    """从文件或 stdin 读取文本（JS），绕开命令行 shell 引号转义问题。

    与 ``rdb_command`` 的 ``--sql-file`` 同一模式：含引号/特殊字符/超长的
    自定义提取 JS 在 Windows shell 下经 argv 传递易被剥离，改走文件/stdin 可靠。

    - ``path`` 为 None/空：返回 None（调用方按原参数处理）。
    - ``path == '-'``：读取 stdin 全文。
    - 其它：按 UTF-8（容忍 BOM）读取文件全文，原样返回。
    """
    import sys

    if not path:
        return None
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


class AutotestCommand(Command):
    """
    Web 自动化测试辅助命令。

    支持子命令：
    - probe: DOM 摘要探针（页面压缩成 KB 级 markdown 结构摘要）
    """

    @property
    def name(self) -> str:
        return "autotest"

    @property
    def abbr(self) -> str:
        return "au"

    @property
    def description(self) -> str:
        return "Web 自动化测试辅助（DOM 摘要探针等）"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao autotest <子命令> [选项]\n"
            "\n"
            "子命令:\n"
            "  probe TARGET [选项]   DOM 摘要探针：打开已登录页面，输出 KB 级 markdown 摘要\n"
            "                        （表单项/表格/按钮/弹窗/消息/下拉实际选项），供 AI 省成本了解页面\n"
            "\n"
            "  TARGET 为路由（如 it-asset、#/it-asset）或完整 URL；Git Bash 下用纯路由\n"
            "  （不带开头 # 或 /），否则会被 MSYS 路径转换污染\n"
            "\n"
            "probe 选项:\n"
            "  --account NAME     账号名（登录态槽位），登录态缓存文件名 <account>.json\n"
            "                     （默认: admin；非系统权限角色，多账号用不同账号名）\n"
            "  --base-url URL     被测系统基础地址（默认: 环境变量 BASE_URL）\n"
            "  --auth-dir DIR     登录态缓存目录（默认: .auth）\n"
            "  --username NAME    登录用户名（默认: 环境变量 ADMIN_USERNAME；缓存有效时不使用）\n"
            "  --password PASS    登录密码（默认: 环境变量 ADMIN_PASSWORD）\n"
            "  --captcha CODE     验证码（默认: 环境变量 CAPTCHA_VALUE）\n"
            "  --click-label L    提取前先点击的表单项标签，可重复（展开下拉拿实际选项；\n"
            "                    无 el-select 时按文本点按钮）\n"
            "  --brief            超简略模式：只留骨架（表单项 label/类型/必填、列头、按钮），\n"
            "                    去掉当前值/首行样本/分页数据\n"
            "  --js CODE          自定义提取 JS（表达式或箭头函数），返回其结果的紧凑 JSON，\n"
            "                    替代内置摘要（与 --js-file 二选一；含引号时改用 --js-file）\n"
            "  --js-file PATH     从 UTF-8 文件读取自定义 JS（传 - 读 stdin）\n"
            "  --env-file PATH    显式指定 .env 文件（默认: 当前目录 .env，未命中逐级向上找）\n"
            "  --headless         无头模式运行（默认有头）\n"
            "  --out FILE         摘要同时写入指定文件（UTF-8）\n"
            "\n"
            "环境变量（.env 同名键自动加载，Shell/系统环境变量优先）:\n"
            "  BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD / CAPTCHA_VALUE\n"
            "  HEADLESS 未使用；浏览器内核选择沿用 USE_BUILTIN_CHROMIUM / CHROME_PATH\n"
            "\n"
            "示例:\n"
            "  python -m baibao autotest probe \"#/oa/asset\" --account admin\n"
            "  python -m baibao autotest probe \"#/purchase/stock\" --click-label 供应商\n"
            "\n"
            "规划中（未实现，勿依赖）:\n"
            "  login              预热/刷新指定账号的登录态缓存\n"
            "  doctor             环境自检（chrome / playwright / .env）\n"
            "\n"
            "-h, --help              显示帮助信息"
        )

    def execute(self, ctx: CliContext) -> Any:
        args = ctx.current_args
        if not args:
            self.show_usage()
            return False

        subcommand = args[0]
        subcommand_args = args[1:]

        if subcommand == "probe":
            return self._probe(ctx, subcommand_args)
        elif subcommand in ("-h", "--help"):
            self.show_usage()
            return True
        elif subcommand in ("login", "doctor"):
            log.error(f"子命令 {subcommand} 规划中尚未实现")
            return False
        else:
            log.error(f"未知子命令: {subcommand}")
            self.show_usage()
            return False

    def _parse_probe_args(self, args: list[str]) -> argparse.Namespace:
        """解析 probe 子命令参数。"""
        parser = argparse.ArgumentParser(
            prog="python -m baibao autotest probe",
            description="DOM 摘要探针：打开已登录页面，输出 KB 级 markdown 结构摘要",
        )
        parser.add_argument(
            "target",
            help="路由（如 it-asset、#/it-asset）或完整 URL；Git Bash 下用纯路由（不带开头 # 或 /）",
        )
        parser.add_argument(
            "--account", default="admin",
            help="账号名（登录态槽位），登录态缓存文件名 <account>.json（默认: admin；非系统权限角色）",
        )
        parser.add_argument(
            "--base-url", dest="base_url", default=None,
            help="被测系统基础地址（默认: 环境变量 BASE_URL）",
        )
        parser.add_argument(
            "--auth-dir", dest="auth_dir", default=".auth",
            help="登录态缓存目录（默认: .auth）",
        )
        parser.add_argument(
            "--username", default=None,
            help="登录用户名（默认: 环境变量 ADMIN_USERNAME）",
        )
        parser.add_argument(
            "--password", default=None,
            help="登录密码（默认: 环境变量 ADMIN_PASSWORD）",
        )
        parser.add_argument(
            "--captcha", default=None,
            help="验证码（默认: 环境变量 CAPTCHA_VALUE）",
        )
        parser.add_argument(
            "--click-label", dest="click_label", action="append", default=None,
            help="提取前先点击的表单项标签（可重复，用于展开下拉拿实际选项）",
        )
        parser.add_argument(
            "--brief", action="store_true",
            help="超简略模式：只留骨架（表单项 label/类型/必填、列头、按钮），"
                 "去掉当前值/首行样本/分页数据",
        )
        js_group = parser.add_mutually_exclusive_group()
        js_group.add_argument(
            "--js", dest="js", default=None,
            help="自定义提取 JS（表达式或箭头函数），返回其结果的紧凑 JSON，"
                 "替代内置摘要（含引号时改用 --js-file）",
        )
        js_group.add_argument(
            "--js-file", dest="js_file", default=None,
            help="从 UTF-8 文件读取自定义 JS（传 - 读 stdin），适合含引号/特殊字符/超长 JS",
        )
        parser.add_argument(
            "--env-file", dest="env_file", default=None,
            help="显式指定 .env 文件路径（默认: 当前目录 .env，未命中则逐级向上查找）",
        )
        parser.add_argument(
            "--headless", action="store_true",
            help="无头模式运行（默认有头）",
        )
        parser.add_argument(
            "--out", default=None,
            help="摘要同时写入指定文件（UTF-8）",
        )
        ns = parser.parse_args(args)
        # 互斥必填组保证至多一个；--js-file 时走文件/stdin
        ns.js = ns.js if ns.js is not None else _read_text_source(ns.js_file)
        return ns

    def _probe(self, ctx: CliContext, args: list[str]) -> bool:
        """执行 DOM 摘要探针。"""
        try:
            ns = self._parse_probe_args(args)
        except SystemExit:
            return False

        load_dotenv_if_present(ns.env_file)

        base_url = normalize_base_url(ns.base_url or os.getenv("BASE_URL", ""))
        if not base_url:
            log.error("缺少被测系统地址：传 --base-url 或在 .env/环境变量配置 BASE_URL")
            return False

        try:
            from playwright.sync_api import (
                sync_playwright,  # noqa: F401  # pyright: ignore[reportUnusedImport]
            )
        except ImportError:
            log.error('探针依赖 playwright，请先安装：pip install "baibao[autotest]"')
            return False

        from baibao.autotest.probe import run_probe

        try:
            summary = run_probe(
                ns.target,
                base_url=base_url,
                account=ns.account,
                auth_dir=ns.auth_dir,
                username=ns.username or os.getenv("ADMIN_USERNAME", ""),
                password=ns.password or os.getenv("ADMIN_PASSWORD", ""),
                captcha=ns.captcha or os.getenv("CAPTCHA_VALUE", ""),
                click_labels=ns.click_label or [],
                headless=ns.headless,
                brief=ns.brief,
                extract_js=ns.js,
            )
        except Exception as e:
            log.error(f"探针执行失败: {e}")
            return False

        if ns.out:
            Path(ns.out).write_text(summary, encoding="utf-8")
            log.info(f"摘要已写入 {ns.out}")
        log.info(f"摘要大小：{len(summary.encode('utf-8'))} 字节")

        ctx.print_delim()
        print(summary)
        ctx.print_delim()
        return True

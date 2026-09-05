"""
hookshot 命令 - 通用 Webhook 触发器管理（list / fire / status）。

"钩爪枪，一发即中"：把任意系统的一条触发链接（webhook / 触发器 URL / 一键触发接口）
登记到 ``.baibao/hookshot.config``（JSON，顶层 key=触发器别名），本命令即可列出、
触发、查询——等价于点网页上的触发按钮，不操作浏览器。

**通用工具，不绑定任何具体系统**：成败只按 HTTP 状态码判定（2xx/3xx 成功），
响应体原样输出不做解析——Jpom 的 type=error、状态码 0~7 之类实现细节，
由调用方（AI 技能适配文档）解读，不在本命令内置。

配置自动读取（两级搜索，先找到的为准，与 rdb.config 同语义）：
    1. $CWD/.baibao/hookshot.config   项目级（优先）
    2. ~/.baibao/hookshot.config      用户级（兜底）

使用方式：
    python -m baibao hookshot list                              列出全部触发器（token 打码）
    python -m baibao hookshot fire <别名> [--dry-run] [--yes]   触发（--dry-run 仅预览；env=prod 需 --yes）
    python -m baibao hookshot status <别名>                     只读请求 status_url，输出原始响应

配置示例（UTF-8 无 BOM）：
    {
      "backend-pre": {
        "system": "jpom",
        "trigger_url": "http://.../api/build2/<id>/<token>",
        "status_url": "http://.../api/build_status?id=...&token=...",
        "method": "POST",
        "headers": {"Authorization": "Basic xxx"},
        "env": "prod",
        "note": "pre 环境后端项目"
      }
    }

字段说明：system 仅作标签（调用方按它选适配文档，命令本身不因它改变行为）、
trigger_url 必填（后台原样整条复制，不手工拼参数）、status_url 可选、method
可选默认 POST、headers 可选字典、env 可选（"prod" 触发需 --yes）、note 可选备注。

安全约定：触发 URL 含 token 等同于凭证，配置文件不入 git；本命令所有输出对
疑似凭证打码（query 中键名含 token/secret/key/sign 的参数值、≥16 位十六进制
的路径段、Authorization 等头值）。

退出码：命令失败（配置缺失、别名未匹配、prod 未确认、请求失败）返回 False →
框架以 exit 1 退出；成功 exit 0（框架级约定，所有 baibao 命令一致）。
"""

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import ResolveType, fileutil, logutil

log = logutil.getLogger(__name__)

TAG = "[hookshot]"

#: 配置文件相对路径（经 fileutil 两级搜索：当前目录 .baibao/ 优先，~/.baibao/ 兜底）
_CONFIG_FILE = ".baibao/hookshot.config"

#: HTTP 超时（秒）
_TIMEOUT = 30

#: 响应体打印上限（字符）
_MAX_BODY_PRINT = 500

#: 视为成功的 HTTP 状态码（2xx + 常见重定向）
_OK_STATUSES = (200, 201, 202, 204, 302)

#: 疑似凭证的路径段：≥16 位十六进制（常见 id/token 形态），保守打码
_HEX_SEGMENT_RE = re.compile(r"^[0-9a-fA-F]{16,}$")

#: query 参数名含这些关键词时，其值视为凭证打码
_SENSITIVE_PARAM_KEYS = ("token", "secret", "key", "sign")


def _load_hooks() -> dict[str, dict[str, Any]] | None:
    """读取并校验 hookshot.config；失败时打引导日志并返回 None。"""
    try:
        content = fileutil.read_text(_CONFIG_FILE,
                                     search_dirs=[ResolveType.CURRENT, ResolveType.USER])
        hooks: dict[str, dict[str, Any]] = json.loads(content)
    except Exception as e:
        log.error(f"读取配置失败: {e}")
        log.error(f"请创建 {_CONFIG_FILE}（当前目录 .baibao/ 优先，其次 ~/.baibao/；"
                  f"JSON、UTF-8 无 BOM，格式见 hookshot 技能 references/config-example.json）")
        return None
    if not isinstance(hooks, dict) or not hooks:
        log.error(f"配置为空或顶层不是 JSON 对象（应为 {{\"别名\": {{...}}}}）: {_CONFIG_FILE}")
        return None
    broken = [n for n, c in hooks.items()
              if not (isinstance(c, dict) and c.get("trigger_url"))]
    if broken:
        log.error(f"以下触发器缺 trigger_url 或值不是对象: {'、'.join(broken)}")
        return None
    return hooks


def _mask_url(url: str) -> str:
    """打码 URL 中的疑似凭证：query 里键名敏感的参数值、≥16 位十六进制的路径段。"""
    parts = urllib.parse.urlsplit(url)
    query = ""
    if parts.query:
        kept = [(k, "***" if any(s in k.lower() for s in _SENSITIVE_PARAM_KEYS) else v)
                for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
        query = "&".join(f"{k}={v}" for k, v in kept)
    segs = ["***" if _HEX_SEGMENT_RE.fullmatch(s) else s for s in parts.path.split("/")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/".join(segs), query, parts.fragment))


def _mask_header_value(value: str) -> str:
    """打码请求头值：Authorization 保留 scheme、打码凭证。"""
    if " " in value:
        scheme, _, _ = value.partition(" ")
        return f"{scheme} ***"
    return "***"


def _print_list(hooks: dict[str, dict[str, Any]]) -> None:
    print(f"已配置 {len(hooks)} 个触发器:")
    for name, cfg in sorted(hooks.items()):
        system = cfg.get("system", "generic")
        env = cfg.get("env", "")
        line = f"  {name:<24} {system:<8} {_mask_url(cfg['trigger_url'])}"
        if env:
            line += f"  [env={env}]"
        print(line)
        if cfg.get("note"):
            print(f"  {'':24} 备注: {cfg['note']}")


def _find_alias(hooks: dict[str, dict[str, Any]], name: str) -> str | None:
    """精确匹配别名；未命中做子串模糊匹配，唯一命中放行，多候选/零命中列出并返回 None。"""
    if name in hooks:
        return name
    low = name.lower()
    cands = [t for t in hooks if low in t.lower() or t.lower() in low]
    if len(cands) == 1:
        print(f"{TAG} 唯一模糊匹配: {cands[0]}")
        return cands[0]
    print(f"{TAG} 未找到触发器「{name}」")
    _print_list(hooks)
    if len(cands) > 1:
        print(f"{TAG} 多个候选含该关键词，请用完整别名: {'、'.join(sorted(cands))}")
    return None


def _judge(status: int) -> tuple[bool, str]:
    """通用成败判定：只看 HTTP 状态码（2xx/3xx 成功），不解析响应体。"""
    if status not in _OK_STATUSES:
        return False, f"HTTP {status}"
    return True, ""


def _diagnose(status: int) -> None:
    """按 HTTP 状态码给出通用排查方向（系统特定细节由技能适配文档补充）。"""
    if status in (401, 403):
        print(f"{TAG} 排查: 认证失败——检查条目的 headers 配置是否缺失/过期；具体系统排查见技能适配篇")
    elif status == 404:
        print(f"{TAG} 排查: URL 复制不全 / 名称或 token 错 / 路径层级漏了（重新从后台整条复制）")


def _request(url: str, method: str, headers: dict[str, str]) -> tuple[int, str] | None:
    """发起 HTTP 请求，返回 (状态码, 响应体)；网络错误时打印排查并返回 None。"""
    req = urllib.request.Request(
        url,
        data=b"" if method in ("POST", "PUT", "PATCH") else None,
        method=method,
    )
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("User-Agent", "baibao-hookshot/1.0")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096).decode("utf-8", "replace")
    except urllib.error.URLError as e:
        print(f"{TAG} ❌ 网络错误: {e.reason}")
        print(f"{TAG} 排查: 本机是否可达该地址（内网需连 VPN）、URL 协议与 host 是否正确")
        return None


class HookshotCommand(Command):
    """
    Webhook 触发器管理命令。

    支持子命令：
    - list:   列出已配置的触发器（含备注，token 打码）
    - fire:   触发一个触发器（支持 --dry-run 预览、prod 环境 --yes 门禁）
    - status: 只读请求 status_url 并输出原始响应（不解析）
    """

    @property
    def name(self) -> str:
        return "hookshot"

    @property
    def description(self) -> str:
        return "Webhook 触发器管理（list 列出 / fire 触发 / status 查状态），配置自动读 .baibao/hookshot.config"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao hookshot <子命令> [选项]\n"
            "\n"
            "子命令:\n"
            "  list                                  列出已配置的触发器（token 打码）\n"
            "  fire    <别名> [--dry-run] [--yes]    触发；--dry-run 仅打码预览不发请求；env=prod 需 --yes\n"
            "  status  <别名>                        只读请求 status_url，输出原始响应（解读由调用方负责）\n"
            "\n"
            "选项:\n"
            "  --dry-run  只展示将要发出的请求（URL/头部打码），不实际触发\n"
            "  --yes      prod 确认开关：env=prod 的触发器必须加此参数才触发\n"
            "  -h, --help 显示帮助信息\n"
            "\n"
            "成败判定：HTTP 2xx/3xx 为成功（不解析响应体）；失败返回 False → exit 1\n"
            "配置: .baibao/hookshot.config（当前目录优先，其次 ~/.baibao/；JSON、UTF-8 无 BOM）"
        )

    def execute(self, ctx: CliContext) -> Any:
        args = ctx.current_args
        if not args:
            self.show_usage()
            return False

        sub = args[0]
        if sub in ("-h", "--help"):
            self.show_usage()
            return True
        if sub == "list":
            return self._list()
        if sub == "fire":
            return self._fire(args[1:])
        if sub == "status":
            return self._status(args[1:])

        log.error(f"未知子命令: {sub}")
        self.show_usage()
        return False

    def _list(self) -> bool:
        hooks = _load_hooks()
        if hooks is None:
            return False
        _print_list(hooks)
        return True

    def _fire(self, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog=f"python -m baibao {self.name} fire")
        parser.add_argument("alias", help="触发器别名（见 list 输出）")
        parser.add_argument("--dry-run", action="store_true", help="仅打码预览，不实际触发")
        parser.add_argument("--yes", action="store_true", help="prod 确认开关")
        ns = parser.parse_args(args)

        hooks = _load_hooks()
        if hooks is None:
            return False
        alias = _find_alias(hooks, ns.alias)
        if alias is None:
            return False
        cfg = hooks[alias]

        if str(cfg.get("env", "")).lower() == "prod" and not ns.yes and not ns.dry_run:
            log.error(f"触发器 [{alias}] 标记为 env=prod，拒绝触发。确认无误请加 --yes")
            return False
        return self._do_fire(alias, cfg, ns.dry_run)

    def _do_fire(self, alias: str, cfg: dict[str, Any], dry_run: bool) -> bool:
        method = str(cfg.get("method", "POST")).upper()
        headers = dict(cfg.get("headers") or {})
        url = str(cfg["trigger_url"])

        print(f"{TAG} 触发器: {alias} ({cfg.get('system', 'generic')})")
        if cfg.get("note"):
            print(f"{TAG} 备注: {cfg['note']}")
        print(f"{TAG} 请求: {method} {_mask_url(url)}")
        for hk, value in headers.items():
            print(f"{TAG} 头部: {hk}: {_mask_header_value(str(value))}")
        if dry_run:
            print(f"{TAG} DRY-RUN: 未实际触发")
            return True

        result = _request(url, method, headers)
        if result is None:
            return False
        status, body = result
        print(f"{TAG} HTTP {status}")
        if body:
            print(f"{TAG} 响应: {body[:_MAX_BODY_PRINT]}")
        ok, detail = _judge(status)
        if ok:
            print(f"{TAG} ✅ 触发成功（HTTP 判定；响应体含义由调用方按系统解读）")
            return True
        print(f"{TAG} ❌ 触发失败: {detail}")
        _diagnose(status)
        return False

    def _status(self, args: list[str]) -> bool:
        parser = argparse.ArgumentParser(prog=f"python -m baibao {self.name} status")
        parser.add_argument("alias", help="触发器别名（见 list 输出）")
        ns = parser.parse_args(args)

        hooks = _load_hooks()
        if hooks is None:
            return False
        alias = _find_alias(hooks, ns.alias)
        if alias is None:
            return False
        cfg = hooks[alias]

        url = cfg.get("status_url")
        if not url:
            log.error(f"触发器 [{alias}] 未配置 status_url。"
                      f"请在 hookshot.config 该条目加 \"status_url\" 字段（从系统后台复制只读状态查询地址）")
            return False

        print(f"{TAG} 触发器: {alias} 查询状态（只读，不触发）")
        if cfg.get("note"):
            print(f"{TAG} 备注: {cfg['note']}")
        print(f"{TAG} 请求: GET {_mask_url(str(url))}")

        result = _request(str(url), "GET", {})
        if result is None:
            return False
        status, body = result
        print(f"{TAG} HTTP {status}")
        print(f"{TAG} 响应: {body[:_MAX_BODY_PRINT] if body else '(空)'}")
        ok, detail = _judge(status)
        if ok:
            print(f"{TAG} ✅ 查询成功（响应为原始数据，含义由调用方按系统解读）")
            return True
        print(f"{TAG} ❌ 查询失败: {detail}")
        _diagnose(status)
        return False

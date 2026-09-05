"""
agent_image 命令 - 从 AI agent 的会话库里取"最新一张粘贴图"。

很多 AI agent（opencode、codex、workbuddy 等）会把用户粘贴的图片以 base64
内联存入各自的会话存储（文件名常被丢弃，且消息文本里不暴露路径）。当模型本身
不支持视觉输入时，无法直接看到这些图。本命令按 ``--agent`` 选择对应的适配器，
从该 agent 的会话库取最新一条图片记录，base64 解码落地为临时图片文件，向 stdout
输出其绝对路径，供下游（OCR、文档生成等）继续处理。

扩展新 agent：实现一个 :class:`AgentImageAdapter` 子类并注册到 :data:`_AGENTS`。

以只读方式打开数据库（URI ``mode=ro``），绝不锁住正在写入的 agent 进程。

使用方式：
    python -m baibao agent_image                                默认 agent=opencode，取最新图（≤120s）
    python -m baibao agent_image --agent opencode               显式指定 agent
    python -m baibao agent_image --max-age 0                    不限时效，取库里最新一张
    python -m baibao agent_image --db <path>                    指定会话库路径
    python -m baibao agent_image --out-dir <dir>               指定临时图存放目录
    python -m baibao agent_image --list                        仅列出最近 5 条元信息（调试）
    python -m baibao agent_image --delim __IMG__                用分隔符包裹结果，便于精准截取
"""

import argparse
import base64
import binascii
import json
import os
import time
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.db import RdbCfg, RdbManager, SqliteClient
from pykunlun.util import logutil

log = logutil.getLogger(__name__)

# 默认时效窗口（秒）：只取该秒数内的图片记录，避免取到很久以前的粘贴图。
DEFAULT_MAX_AGE_SEC = 120

# 注册到 RdbManager 的实例别名（局部使用，避免与全局 rdb 实例冲突）
_INSTANCE_NAME = "agent_image"

# image MIME → 扩展名；未命中回退 png
_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
}


def _default_out_dir() -> str:
    """临时图默认存放目录：系统临时目录。"""
    return os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"


def _ext_for(mime: str | None) -> str:
    """按 MIME 返回扩展名，未知类型回退 png。"""
    return _MIME_EXT.get((mime or "").lower(), "png")


def _decode_data_url(url: str, out_dir: str, ext: str, agent: str) -> str | None:
    """
    把 ``data:image/...;base64,XXXX`` 解码为临时图片文件。

    Args:
        url: 图片的 data URL。
        out_dir: 临时文件存放目录。
        ext: 文件扩展名（不含点）。
        agent: 来源 agent 名（用于命名临时文件，便于辨识）。

    Returns:
        写入的临时文件绝对路径；解码失败返回 ``None``。
    """
    if "," not in url:
        return None
    b64 = url.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return None
    if not raw:
        return None
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{agent}_img_{int(time.time() * 1000)}.{ext}")
    with open(path, "wb") as f:
        f.write(raw)
    return path


# region ======== agent 适配器（扩展点） ========

class AgentImageAdapter:
    """
    AI agent 会话图片提取适配器（抽象基类）。

    每个 agent 的会话存储位置、表结构、字段命名各异，本类把这些差异收敛为
    统一接口；新增 agent 支持只需继承本类并覆盖几个方法，再注册到 :data:`_AGENTS`。

    约定：本适配器面向"用 SQLite 存储会话、图片以 base64 内联（data URL）
    存于某张表"的 agent。存储模型完全不同（如纯文件、KV 库）的 agent，
    可覆盖 :meth:`fetch_latest` / :meth:`list_recent` 整体重写。

    需要实现/覆盖：
      - :attr:`name`               : agent 标识（如 ``opencode``）。
      - :meth:`default_db_path`    : 默认会话库路径（供 ``--db`` 缺省时使用）。
      - :meth:`fetch_latest_sql`   : 取最新一条图片的 SQL（含时效过滤）。
      - :meth:`list_sql`           : 列最近若干条的 SQL。
      - :meth:`parse_row`          : 从查询行解析出 ``(data_url, mime)``。
      - :meth:`format_list_row`    : 列表模式下单行的可读格式（可选，有默认）。
    """

    #: agent 标识（子类以类级常量提供）
    name: str = ""

    def default_db_path(self) -> str:
        """默认会话库路径。子类按 agent 的约定返回。"""
        raise NotImplementedError

    def fetch_latest_sql(self, max_age_sec: int) -> tuple[str, tuple[Any, ...]]:
        """
        构造"取最新一条图片记录"的 SQL 与参数。

        Args:
            max_age_sec: 时效秒；0 表示不限。

        Returns:
            ``(sql, params)`` 元组；``params`` 为空时返回空元组 ``()``。
        """
        raise NotImplementedError

    def list_sql(self) -> tuple[str, tuple[Any, ...]]:
        """构造"列最近若干条图片记录"的 SQL 与参数（供 ``--list``）。"""
        raise NotImplementedError

    def parse_row(self, row: dict[str, Any]) -> tuple[str, str | None]:
        """
        从查询行解析出图片信息。

        Args:
            row: :meth:`RdbManager.query` 返回的字典行（含 ``fetch_latest_sql`` 选出的列）。

        Returns:
            ``(data_url, mime)``；``mime`` 可为 ``None``。
        """
        raise NotImplementedError

    def format_list_row(self, row: dict[str, Any], now_ms: float) -> str:
        """
        列表模式下单行的可读格式（``--list`` 输出）。

        默认实现假设行含 ``time_created`` 字段（毫秒时间戳）并打印 ``age=``；
        子类可覆盖以输出更贴合该 agent 的列。默认除 ``time_created`` 外的列
        以 ``key=value`` 形式追加。
        """
        parts: list[str] = []
        age_s = "?"
        tc = row.get("time_created")
        if isinstance(tc, (int, float)):
            age_s = str(int((now_ms - tc) / 1000))
        parts.append(f"age={age_s}s")
        for k, v in row.items():
            if k == "time_created":
                continue
            parts.append(f"{k}={v}")
        return " ".join(parts)

    # 以下两个方法提供基于 SQL 的默认实现，子类通常无需覆盖；
    # 存储模型特殊的 agent 可整体覆盖。

    def fetch_latest(self, mgr: RdbManager, instance_name: str,
                     max_age_sec: int) -> tuple[str, str | None] | None:
        """取最新一条图片记录并解析为 ``(data_url, mime)``；未命中返回 ``None``。"""
        sql, params = self.fetch_latest_sql(max_age_sec)
        rows = mgr.query(sql, params or None, name=instance_name)
        if not rows:
            return None
        return self.parse_row(rows[0])

    def list_recent(self, mgr: RdbManager, instance_name: str) -> list[dict[str, Any]]:
        """返回最近若干条图片记录（字典列表）。"""
        sql, params = self.list_sql()
        return mgr.query(sql, params or None, name=instance_name)


class _OpencodeAdapter(AgentImageAdapter):
    """
    opencode 会话图片适配器。

    opencode 把粘贴图以 ``type=file``、``mime=image/*``、``url=data:image/...;base64,...``
    存入 ``part`` 表；文件名通常被丢弃成 ``image.png``。按 ``time_created`` 倒序取最新。
    会话库默认位于 ``~/.local/share/opencode/opencode.db``。
    """

    name = "opencode"

    def default_db_path(self) -> str:
        return os.path.join(os.path.expanduser("~"), ".local", "share", "opencode", "opencode.db")

    def fetch_latest_sql(self, max_age_sec: int) -> tuple[str, tuple[Any, ...]]:
        sql = (
            "SELECT data, time_created FROM part "
            "WHERE json_extract(data,'$.type')='file' "
            "AND json_extract(data,'$.mime') LIKE 'image/%'"
        )
        params: tuple[Any, ...] = ()
        if max_age_sec and max_age_sec > 0:
            threshold_ms = time.time() * 1000 - max_age_sec * 1000
            sql += " AND time_created >= ?"
            params = (threshold_ms,)
        sql += " ORDER BY time_created DESC LIMIT 1"
        return sql, params

    def list_sql(self) -> tuple[str, tuple[Any, ...]]:
        return (
            """SELECT time_created, session_id,
                      json_extract(data,'$.mime') AS mime,
                      json_extract(data,'$.filename') AS filename,
                      length(json_extract(data,'$.url')) AS url_len
               FROM part
               WHERE json_extract(data,'$.type')='file'
                 AND json_extract(data,'$.mime') LIKE 'image/%'
               ORDER BY time_created DESC LIMIT 5""",
            (),
        )

    def parse_row(self, row: dict[str, Any]) -> tuple[str, str | None]:
        data = json.loads(row["data"])
        return data.get("url", ""), data.get("mime")


#: 已注册的 agent 适配器表：agent 名 → 适配器实例。
#: 新增 agent = 实现一个 AgentImageAdapter 子类 + 在此注册。
#: 例如未来：
#:     _AGENTS["codex"] = _CodexAdapter()
#:     _AGENTS["workbuddy"] = _WorkbuddyAdapter()
_AGENTS = {
    "opencode": _OpencodeAdapter(),
}

# endregion


# 模块级 RdbManager 单例：避免每次调用重复注册 SqliteClient。
# 不复用 baibao.db.rdb 的全局 rdb 实例——agent 会话库是临时外部库，
# 不应常驻到全局配置注册表。
_manager: RdbManager | None = None


def _get_manager() -> RdbManager:
    """返回已注册 SqliteClient 的局部 RdbManager 单例。"""
    global _manager
    if _manager is None:
        _manager = RdbManager()
        _manager.register_client_class(SqliteClient)
    return _manager


class AgentImageCommand(Command):
    """
    从 AI agent 会话库取最新一张粘贴图。

    按 ``--agent`` 选择适配器，从该 agent 的会话库取最新一条图片记录，
    base64 解码落地为临时图片文件，输出其绝对路径。以 URI ``mode=ro`` 只读打开
    数据库，绝不锁住正在写入的 agent 进程。

    默认结果路径走 stdout、诊断日志走 stderr；``--delim`` 可包裹结果便于精准截取。
    """

    @property
    def name(self) -> str:
        return "agent_image"

    @property
    def description(self) -> str:
        agents = ", ".join(_AGENTS.keys())
        return f"从 AI agent 会话库取最新一张粘贴图，解码为本地文件并输出路径（支持: {agents}）"

    @property
    def usage(self) -> str:
        agents = ", ".join(_AGENTS.keys())
        default_agent = next(iter(_AGENTS.keys()))
        return (
            f"python -m baibao {self.name} [选项]  （agent: {agents}，默认: {default_agent}；"
            f"时效窗口 {DEFAULT_MAX_AGE_SEC}s）\n"
            "\n"
            "选项:\n"
            f"      --agent NAME            来源 agent（{agents}，默认: {default_agent}）\n"
            f"      --max-age N             只取该秒数内的图片；0=不限（默认: {DEFAULT_MAX_AGE_SEC}）\n"
            "      --db PATH               会话库路径（默认按 --agent 推断）\n"
            "      --out-dir PATH          解码后临时图存放目录（默认: 系统临时目录）\n"
            "      --list                  仅列出最近 5 条图片记录元信息，不解码\n"
            "      --delim STR             结果分隔符：设则用其在 stdout 结果前后各占一行包裹，\n"
            "                              便于在夹杂日志时精准截取（由调用方保证唯一）\n"
            "  -h, --help                  显示帮助信息\n"
            "\n"
            "示例:\n"
            f"  python -m baibao {self.name}                           # 取 opencode 最新粘贴图（≤{DEFAULT_MAX_AGE_SEC}s）\n"
            f"  python -m baibao {self.name} --agent opencode --max-age 0   # 不限时效，取库里最新\n"
            f"  python -m baibao {self.name} --list                    # 看候选\n"
            f"  python -m baibao {self.name} --delim __IMG__ 2>$null\n"
        )

    # region ======== 参数解析 ========

    def _parse_args(self, args: list[str]) -> argparse.Namespace:
        """解析命令行参数。"""
        parser = argparse.ArgumentParser(
            prog=f"python -m baibao {self.name}",
            description=self.description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "--agent",
            choices=list(_AGENTS.keys()),
            default=next(iter(_AGENTS.keys())),
            help=f"来源 agent（{', '.join(_AGENTS.keys())}，默认: {next(iter(_AGENTS.keys()))}）",
        )
        parser.add_argument(
            "--max-age",
            type=int,
            default=DEFAULT_MAX_AGE_SEC,
            help=f"只取该秒数内的图片；0=不限（默认: {DEFAULT_MAX_AGE_SEC}）",
        )
        parser.add_argument(
            "--db",
            default=None,
            help="会话库路径（默认按 --agent 推断）",
        )
        parser.add_argument(
            "--out-dir",
            default=None,
            help="解码后临时图存放目录（默认: 系统临时目录）",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="仅列出最近 5 条图片记录元信息，不解码",
        )
        return parser.parse_args(args)

    # endregion

    # region ======== 执行入口 ========

    def execute(self, ctx: CliContext) -> Any:
        """
        执行取图命令。

        Returns:
            True 表示成功取到图并输出路径；False 表示库不存在、时效内无图或解码失败。
        """
        args = ctx.current_args

        def _emit(payload: str) -> None:
            """用 ctx.delim_str 在 stdout 包裹 payload 输出；未设 delim_str 时仅输出 payload。"""
            ctx.print_delim()
            print(payload)
            ctx.print_delim()

        try:
            ns = self._parse_args(args)
        except SystemExit:
            return False

        adapter = _AGENTS[ns.agent]
        db_path = ns.db if ns.db else adapter.default_db_path()

        # 库不存在：直接失败（不尝试创建——只读连接本就不该建库）
        if not os.path.exists(db_path):
            log.error(f"[{ns.agent}] 会话库不存在: {db_path}")
            _emit(f"(未找到图片：[{ns.agent}] 会话库不存在 - {db_path})")
            return False

        mgr = _get_manager()
        try:
            mgr.register(
                _INSTANCE_NAME,
                RdbCfg(db_type="sqlite", database=db_path, read_only=True),
            )
        except Exception as e:
            log.error(f"[{ns.agent}] 注册数据库实例失败: {e}")
            _emit(f"(未找到图片：[{ns.agent}] 无法打开会话库；详见 stderr)")
            return False

        # ---- --list：仅列候选 ----
        if ns.list:
            try:
                rows = adapter.list_recent(mgr, _INSTANCE_NAME)
                ctx.print_delim()
                if not rows:
                    print(f"([{ns.agent}] 库中无图片记录)")
                else:
                    now_ms = time.time() * 1000
                    for r in rows:
                        print(adapter.format_list_row(r, now_ms))
                ctx.print_delim()
            except Exception as e:
                log.error(f"[{ns.agent}] 列出图片记录失败: {e}")
                return False
            return True

        # ---- 取最新一条 ----
        try:
            parsed = adapter.fetch_latest(mgr, _INSTANCE_NAME, ns.max_age)
        except Exception as e:
            log.error(f"[{ns.agent}] 查询图片记录失败: {e}")
            _emit(f"(未找到图片：[{ns.agent}] 查询会话库失败；详见 stderr)")
            return False

        if not parsed:
            log.warning("[%s] 时效内未找到图片记录（max_age=%ss）", ns.agent, ns.max_age)
            _emit(f"(未找到图片：[{ns.agent}] 会话库内最近 {ns.max_age}s 无粘贴图)")
            return False

        # ---- 解码落地 ----
        url, mime = parsed
        ext = _ext_for(mime)
        out_dir = ns.out_dir or _default_out_dir()
        path = _decode_data_url(url, out_dir, ext, ns.agent)
        if not path:
            log.error("[%s] 图片记录的 data URL 解码失败", ns.agent)
            _emit(f"(未找到图片：[{ns.agent}] data URL 解码失败；详见 stderr)")
            return False

        log.info("[%s] 已解码图片记录 → %s", ns.agent, path)
        _emit(path)
        return True

    # endregion

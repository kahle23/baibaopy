"""
数据库操作命令 - 提供 SQL 查询、SQL 执行、列出已注册数据库等功能。
"""

import argparse
import csv
import io
import json
import sys
from datetime import date, datetime, time
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.db.rdb import rdb_mgr

log = logutil.getLogger(__name__)


def _read_text_source(path: str | None) -> str | None:
    """从文件或 stdin 读取文本（SQL），绕开命令行 shell 引号转义问题。

    供 ``--sql-file`` 使用：含双引号/特殊字符/超长的 SQL 在 Windows PowerShell 等
    shell 下经 argv 传递时易被 CRT 剥离（解析发生在 Python 运行之前），改走文件/stdin 可靠。

    - ``path`` 为 None/空：返回 None（调用方按原参数处理）。
    - ``path == '-'``：读取 stdin 全文。
    - 其它：按 UTF-8（容忍 BOM）读取文件全文，原样返回（不去除换行）。
    """
    if not path:
        return None
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


class _CustomEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，处理日期时间类型。"""

    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        return super().default(o)


class RdbCommand(Command):
    """
    数据库操作命令。

    支持子命令：
    - list: 列出已注册的数据库名称
    - query: 执行 SQL 查询并显示结果
    - execute: 执行 SQL 语句（INSERT/UPDATE/DELETE/DDL）
    """

    @property
    def name(self) -> str:
        return "rdb"

    @property
    def abbr(self) -> str:
        return "r"

    @property
    def description(self) -> str:
        return "数据库操作（查询/执行/列出数据库）"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao rdb <子命令> [选项]\n"
            "\n"
            "子命令:\n"
            "  list                                         列出已注册的数据库名称\n"
            "  query   --sql SQL | --sql-file PATH [--db=NAME] [--format=FORMAT]  执行 SQL 查询\n"
            "  execute --sql SQL | --sql-file PATH [--db=NAME]                    执行 SQL 语句（写操作）\n"
            "\n"
            "选项:\n"
            "  --db     NAME   指定数据库实例名（默认: default）\n"
            "  --sql    SQL    内联 SQL（与 --sql-file 二选一；含双引号时改用 --sql-file）\n"
            "  --sql-file PATH 从 UTF-8 文件读取 SQL（传 - 读 stdin）；含引号/特殊字符/超长 SQL 用此项\n"
            "  --format FORMAT 输出格式: json|jsonl|csv|table（默认: jsonl）\n"
            "  --delim  STR    结果分隔符：设则用其在 stdout 结果前后各占一行包裹，\n"
            "                  便于在夹杂日志时精准截取（由调用方保证唯一）\n"
            "  -h, --help      显示帮助信息"
        )

    def execute(self, ctx: CliContext) -> Any:
        args = ctx.current_args
        if not args:
            self.show_usage()
            return False

        subcommand = args[0]
        subcommand_args = args[1:]

        if subcommand == "list":
            return self._list_databases(ctx)
        elif subcommand == "query":
            return self._execute_query(ctx, subcommand_args)
        elif subcommand == "execute":
            return self._execute_sql(subcommand_args)
        elif subcommand in ("-h", "--help"):
            self.show_usage()
            return True
        else:
            log.error(f"未知子命令: {subcommand}")
            self.show_usage()
            return False

    def _list_databases(self, ctx: CliContext) -> bool:
        """列出已注册的数据库名称。"""
        loader = rdb_mgr.get_config_loader()
        if loader:
            loader(rdb_mgr, "")
        names = rdb_mgr.get_registered_names()
        if not names:
            log.info("没有已注册的数据库")
            return True

        ctx.print_delim()
        print("已注册的数据库:")
        for name in sorted(names):
            print(f"  - {name}")
        ctx.print_delim()
        return True

    def _parse_query_args(self, args: list[str]) -> argparse.Namespace:
        """解析 query 子命令参数。"""
        parser = argparse.ArgumentParser(
            prog="python -m baibao rdb query",
            description="执行 SQL 查询",
        )
        sql_group = parser.add_mutually_exclusive_group(required=True)
        sql_group.add_argument(
            "--sql",
            dest="sql",
            default=None,
            help="内联 SQL（含双引号时仍会被 shell argv 剥离，请改用 --sql-file）",
        )
        sql_group.add_argument(
            "--sql-file",
            dest="sql_file",
            default=None,
            help="从 UTF-8 文件读取 SQL（传 - 读 stdin），适合含引号/特殊字符/超长 SQL",
        )
        parser.add_argument("--db", default=None, help="数据库实例名（默认: default）")
        parser.add_argument(
            "--format",
            choices=["json", "jsonl", "csv", "table"],
            default="jsonl",
            help="输出格式（默认: jsonl）",
        )
        ns = parser.parse_args(args)
        # 互斥必填组保证恰好一个；--sql-file 时走文件/stdin
        ns.sql = ns.sql if ns.sql is not None else _read_text_source(ns.sql_file)
        return ns

    def _parse_execute_args(self, args: list[str]) -> argparse.Namespace:
        """解析 execute 子命令参数。"""
        parser = argparse.ArgumentParser(
            prog="python -m baibao rdb execute",
            description="执行 SQL 语句（写操作）",
        )
        sql_group = parser.add_mutually_exclusive_group(required=True)
        sql_group.add_argument(
            "--sql",
            dest="sql",
            default=None,
            help="内联 SQL（含双引号时仍会被 shell argv 剥离，请改用 --sql-file）",
        )
        sql_group.add_argument(
            "--sql-file",
            dest="sql_file",
            default=None,
            help="从 UTF-8 文件读取 SQL（传 - 读 stdin），适合含引号/特殊字符/超长 SQL",
        )
        parser.add_argument("--db", default=None, help="数据库实例名（默认: default）")
        ns = parser.parse_args(args)
        ns.sql = ns.sql if ns.sql is not None else _read_text_source(ns.sql_file)
        return ns

    def _execute_query(self, ctx: CliContext, args: list[str]) -> bool:
        """执行 SQL 查询。"""
        try:
            ns = self._parse_query_args(args)
        except SystemExit:
            return False

        try:
            db_name = ns.db if ns.db else None
            log.info(f"执行查询: {ns.sql}")
            result = rdb_mgr.query(ns.sql, name=db_name)

            if not result:
                log.info("查询结果为空")
                return True

            ctx.print_delim()
            self._display_result(result, ns.format)
            ctx.print_delim()
            return True
        except Exception as e:
            log.error(f"查询失败: {e}")
            return False

    def _execute_sql(self, args: list[str]) -> bool:
        """执行 SQL 语句（写操作）。"""
        try:
            ns = self._parse_execute_args(args)
        except SystemExit:
            return False

        try:
            db_name = ns.db if ns.db else None
            log.info(f"执行 SQL: {ns.sql}")
            affected_rows = rdb_mgr.execute(ns.sql, name=db_name)
            log.info(f"执行成功，受影响行数: {affected_rows}")
            return True
        except Exception as e:
            log.error(f"执行失败: {e}")
            return False

    def _display_result(self, result: list[dict[str, Any]], output_format: str) -> None:
        """显示查询结果。"""
        if output_format == "json":
            self._display_json(result)
        elif output_format == "jsonl":
            self._display_jsonl(result)
        elif output_format == "csv":
            self._display_csv(result)
        elif output_format == "table":
            self._display_table(result)

    def _display_json(self, result: list[dict[str, Any]]) -> None:
        """JSON 格式输出。"""
        print(json.dumps(result, ensure_ascii=False, indent=2, cls=_CustomEncoder))

    def _display_jsonl(self, result: list[dict[str, Any]]) -> None:
        """JSONL 格式输出 - 每行一个 JSON 对象。"""
        for row in result:
            print(json.dumps(row, ensure_ascii=False, cls=_CustomEncoder))

    def _display_csv(self, result: list[dict[str, Any]]) -> None:
        """CSV 格式输出。"""
        if not result:
            return

        output = io.StringIO()
        columns = list(result[0].keys())
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(result)
        print(output.getvalue(), end="")

    def _display_table(self, result: list[dict[str, Any]]) -> None:
        """表格格式输出。"""
        if not result:
            return

        columns = list(result[0].keys())

        col_widths: dict[str, int] = {}
        for col in columns:
            col_widths[col] = max(
                len(str(col)),
                max(len(str(row.get(col, ""))) for row in result),
            )

        header = " | ".join(str(col).ljust(col_widths[col]) for col in columns)
        print(header)
        print("-" * len(header))

        for row in result:
            row_str = " | ".join(
                str(row.get(col, "")).ljust(col_widths[col]) for col in columns
            )
            print(row_str)

        print(f"\n共 {len(result)} 条记录")

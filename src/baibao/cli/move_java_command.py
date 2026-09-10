"""
move_java 命令 - 迁移 Java 源文件到新包（自动改 package + 同步 import）。

支持场景：
    - 包重构（如把散落的 Demo 类合并到统一的 demo 子包）
    - main <-> test 之间跨源根迁移
    - 修正测试包路径与被测类不一致

批量迁移：用 ``--map <csv_file>`` 指定一个映射文件，每行格式：
    <src_relpath>|<dest_relpath>[|<dest_root>]
（以 ``#`` 开头的行和空行被忽略）

使用方式：
    # 单文件迁移
    python -m baibao move_java store/code/csv/ApacheCsvDemo.java store/code/demo/csv/ApacheCsvDemo.java

    # 跨源根迁移（main -> test）
    python -m baibao move_java store/code/barcode/QRCodeDemo.java \\
                             store/code/demo/barcode/QRCodeDemo.java \\
                             --dest-root src/test/java

    # 批量迁移（CSV 映射文件）
    python -m baibao move_java --map moves.csv

    # 预演（不写盘）
    python -m baibao move_java <src> <dest> --dry-run

CSV 文件示例（moves.csv）：
    # 普通迁移
    store/code/csv/ApacheCsvDemo.java|store/code/demo/csv/ApacheCsvDemo.java
    # 跨源根迁移（第三列为 dest_root）
    store/code/barcode/QRCodeDemo.java|store/code/demo/barcode/QRCodeDemo.java|src/test/java
"""

import argparse
import os
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.code.java_move import JavaMove, move_java_batch, move_java_file

log = logutil.getLogger(__name__)

DEFAULT_ROOTS = ["src/main/java", "src/test/java"]


class MoveJavaCommand(Command):
    """
    迁移 Java 源文件到新包（自动改 package + 同步 import）。

    支持单文件迁移和批量迁移（CSV 映射文件）。可以跨源根迁移
    （如 main -> test），通过 ``--dest-root`` 或 CSV 第三列指定。
    """

    @property
    def name(self) -> str:
        return "move_java"

    @property
    def description(self) -> str:
        return "迁移 Java 源文件到新包（改 package + 同步 import，支持 main<->test 跨根）"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao move_java <src_relpath> <dest_relpath> [--dest-root ROOT]\n"
            "python -m baibao move_java --map <csv_file>\n"
            "\n"
            "选项:\n"
            "      --map FILE          批量迁移的 CSV 映射文件（每行: src|dest[|dest_root]）\n"
            "      --dest-root ROOT    目标源根（跨根迁移用，如 src/test/java）\n"
            "      --src-roots ROOTS   扫描 import 的源根列表（逗号分隔，默认 src/main/java,src/test/java）\n"
            "      --dry-run           预演模式（不写盘）\n"
            "  -h, --help              显示帮助信息\n"
            "\n"
            "示例:\n"
            "  python -m baibao move_java store/code/csv/A.java store/code/demo/csv/A.java\n"
            "  python -m baibao move_java store/code/X.java store/code/test/X.java --dest-root src/test/java\n"
            "  python -m baibao move_java --map moves.csv\n"
            "  python -m baibao move_java --map moves.csv --dry-run\n"
        )

    # region ======== 参数解析 ========
    def _parse_args(self, args: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog=f"python -m baibao {self.name}",
            description=self.description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "src", nargs="?", help="源文件相对路径（如 store/code/csv/A.java）"
        )
        parser.add_argument(
            "dest", nargs="?", help="目标文件相对路径"
        )
        parser.add_argument(
            "--map", metavar="FILE",
            help="批量迁移的 CSV 映射文件（每行: src|dest[|dest_root]，# 开头为注释）",
        )
        parser.add_argument(
            "--dest-root", default=None,
            help="目标源根（跨根迁移用，如 src/test/java）",
        )
        parser.add_argument(
            "--src-roots", default=None,
            help="扫描 import 的源根列表（逗号分隔，默认 src/main/java,src/test/java）",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="预演模式（不写盘）",
        )
        return parser.parse_args(args)
    # endregion

    def execute(self, ctx: CliContext) -> Any:
        ns = self._parse_args(ctx.current_args)
        src_roots = (ns.src_roots.split(",") if ns.src_roots else DEFAULT_ROOTS)

        if ns.map:
            return self._run_batch(ns.map, src_roots, ns.dry_run)

        if not ns.src or not ns.dest:
            log.error("必须提供 <src> <dest>，或使用 --map <csv_file>")
            return False

        moved = move_java_file(
            ns.src, ns.dest, src_roots,
            dest_root=ns.dest_root, dry_run=ns.dry_run,
        )
        if moved:
            log.info("[OK] %s -> %s (dry_run=%s)", ns.src, ns.dest, ns.dry_run)
        else:
            log.warning("[SKIP] source not found or src==dest: %s", ns.src)
        return moved

    def _run_batch(self, csv_path: str, src_roots: list[str], dry_run: bool) -> bool:
        if not os.path.exists(csv_path):
            log.error("映射文件不存在: %s", csv_path)
            return False
        moves: list[JavaMove] = []
        with open(csv_path, "r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) < 2:
                    log.warning("[SKIP] %s:%d 格式错误，应为 src|dest[|dest_root]: %s",
                                csv_path, lineno, raw)
                    continue
                src = parts[0].strip()
                dest = parts[1].strip()
                dest_root = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
                moves.append(JavaMove(src, dest, dest_root))
        if not moves:
            log.warning("映射文件中没有有效条目: %s", csv_path)
            return False
        log.info("从 %s 加载了 %d 条迁移条目", csv_path, len(moves))
        ok, _skipped = move_java_batch(moves, src_roots, dry_run=dry_run)
        return ok > 0

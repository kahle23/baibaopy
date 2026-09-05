"""
Mojibake 命令 - 检测/修复源文件中的乱码（UTF-8 当 GBK 解读型）。

针对中文源文件最常见的乱码类型：**UTF-8 字节被当作 GBK/CP936 解读后又存成 UTF-8**。
这种乱码看起来像一堆奇怪的中文字符（如 "鎸囧畾闆嗙兢鍚嶇О"），可以通过往返还原法修复：
把乱码文本按 GB18030 编码取字节，再按 UTF-8 解码即可得到原始中文。

为何用 GB18030 而非 GBK：
    GBK 无法编码 PUA (U+E000-U+F8FF) 区字符。Windows CP936 把一些 GBK 无映射的
    字节序列替换为 PUA 码点。GB18030 是 GBK 超集，覆盖全部 Unicode 码点，
    可以正确还原含 PUA 字符的乱码段。

使用方式：
    # 检测（默认只检测不修改）
    python -m baibao mojibake                          # 扫描当前目录下所有 .java
    python -m baibao mojibake path/to/project          # 扫描指定目录
    python -m baibao mojibake path/to/File.java        # 扫描单个文件
    python -m baibao mojibake path --suffix .txt       # 自定义后缀（默认 .java）

    # 修复
    python -m baibao mojibake --fix                    # 修复所有检测到的文件
    python -m baibao mojibake File.java --fix          # 修复单个文件
    python -m baibao mojibake --fix --dry-run          # 预览将要修复的内容（不写盘）
"""

import argparse
import os
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.text.mojibake import clean_fffd_file, fix_file, scan_file, scan_tree

log = logutil.getLogger(__name__)


class MojibakeCommand(Command):
    """
    检测/修复源文件中的 mojibake 乱码（UTF-8 当 GBK 解读型）。

    默认只检测不修改；加 ``--fix`` 在原文件就地修复；加 ``--dry-run`` 预览。
    走 stdout 输出检测结果（便于管道），日志走 stderr。
    """

    @property
    def name(self) -> str:
        return "mojibake"

    @property
    def description(self) -> str:
        return "检测/修复源文件中的乱码（UTF-8 当 GBK 解读型，中文项目常见）"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao mojibake [路径] [选项]  （默认路径为当前目录）\n"
            "\n"
            "选项:\n"
            "  -f, --fix              修复模式（就地修改原文件）\n"
            "      --clean-fffd MODE  清理 U+FFFD 残留（roundtrip 后留下的不可还原字符）\n"
            "                         MODE 可选 conservative（默认，仅删除孤立 U+FFFD）\n"
            "                         或 aggressive（U+FFFD+'?' 替换为 '：'，约 80% 准确）\n"
            "      --dry-run          预览模式（只打印将发生的改动，不写盘）\n"
            "  -s, --suffix EXT       扫描的文件后缀（默认 .java；可多次指定）\n"
            "  -h, --help             显示帮助信息\n"
            "\n"
            "示例:\n"
            "  python -m baibao mojibake                       # 检测当前目录下所有 .java\n"
            "  python -m baibao mojibake src/                  # 检测 src/ 下所有 .java\n"
            "  python -m baibao mojibake File.java             # 检测单个文件\n"
            "  python -m baibao mojibake --fix src/            # 修复 src/ 下所有文件\n"
            "  python -m baibao mojibake --fix --clean-fffd aggressive src/  # 修复+清理残留\n"
            "  python -m baibao mojibake data/ -s .txt -s .md  # 扫描其他后缀\n"
        )

    # region ======== 参数解析 ========

    def _parse_args(self, args: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog=f"python -m baibao {self.name}",
            description=self.description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "path",
            nargs="?",
            default=".",
            help="要扫描的目录或文件路径（默认当前目录）",
        )
        parser.add_argument(
            "-f", "--fix",
            action="store_true",
            help="修复模式（就地修改原文件）；默认只检测",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="预览模式（只打印将发生的改动，不写盘）；常与 --fix 搭配",
        )
        parser.add_argument(
            "--clean-fffd",
            metavar="MODE",
            choices=["conservative", "aggressive"],
            default=None,
            help="清理 U+FFFD 残留：conservative（仅删除孤立 U+FFFD）或 "
                 "aggressive（U+FFFD+'?' 替换为 '：'，约 80% 准确）",
        )
        parser.add_argument(
            "-s", "--suffix",
            action="append",
            default=None,
            help="扫描的文件后缀（默认 .java；可多次指定以扫描多种类型）",
        )
        return parser.parse_args(args)

    # endregion

    def execute(self, ctx: CliContext) -> Any:
        ns = self._parse_args(ctx.current_args)
        path = ns.path
        suffixes = ns.suffix if ns.suffix else [".java"]

        if not os.path.exists(path):
            log.error("路径不存在: %s", path)
            return False

        actions: list[str] = []
        if ns.fix:
            actions.append("FIX")
        if ns.clean_fffd:
            actions.append("CLEAN-FFFD:" + ns.clean_fffd)
        if not actions:
            actions.append("CHECK")
        mode = ("DRY-RUN-" if ns.dry_run else "") + "/".join(actions)
        log.info("[%s] scanning %s (suffixes=%s)", mode, path, suffixes)

        if os.path.isfile(path):
            return self._process_single(path, ns.fix, ns.clean_fffd, ns.dry_run)
        else:
            return self._process_tree(path, suffixes, ns.fix, ns.clean_fffd, ns.dry_run)

    def _process_single(self, path: str, fix: bool, clean_mode: str | None, dry_run: bool) -> bool:
        sus, total = scan_file(path)
        has_fffd = self._file_has_fffd(path)
        if not sus and not (clean_mode and has_fffd):
            print(f"[OK] {path}")
            return True
        if fix:
            cnt = fix_file(path, dry_run=dry_run)
            action = "would be fixed" if dry_run else "fixed"
            print("[{}] {} : {} run(s) {}".format(
                "DRY-RUN" if dry_run else "FIX", path, cnt, action))
        if clean_mode:
            cnt = clean_fffd_file(path, mode=clean_mode, dry_run=dry_run)
            action = "would be cleaned" if dry_run else "cleaned"
            print("[{}] {} : {} U+FFFD {}".format(
                "DRY-RUN" if dry_run else "CLEAN", path, cnt, action))
        if not fix and not clean_mode and sus:
            print(f"[WARN] {path} ({len(sus)}/{total}):")
            for lineno, line in sus[:5]:
                print(f"  L{lineno}: {line[:120]}")
            if len(sus) > 5:
                print(f"  ... and {len(sus) - 5} more")
        return True

    def _process_tree(self, root: str, suffixes: list[str], fix: bool,
                      clean_mode: str | None, dry_run: bool) -> bool:
        # Step 1: collect files with mojibake or U+FFFD residuals
        targets: set[str] = set()
        for suffix in suffixes:
            for path, sus, _ in scan_tree(root, suffix=suffix):
                targets.add(path)
        if clean_mode:
            for dirpath, _, files in os.walk(root):
                for name in files:
                    if not any(name.endswith(s) for s in suffixes):
                        continue
                    full = os.path.join(dirpath, name)
                    if self._file_has_fffd(full):
                        targets.add(full)
        if not targets:
            print(f"[OK] No mojibake/U+FFFD found under: {root}")
            return True
        print("[{}] Processing {} file(s):\n".format(
            "DRY-RUN" if dry_run else ("FIX/CLEAN" if (fix or clean_mode) else "WARN"),
            len(targets)))
        grand_fix = grand_clean = 0
        for path in sorted(targets):
            if fix:
                cnt = fix_file(path, dry_run=dry_run)
                grand_fix += cnt
            if clean_mode:
                cnt = clean_fffd_file(path, mode=clean_mode, dry_run=dry_run)
                grand_clean += cnt
            if not fix and not clean_mode:
                sus, total = scan_file(path)
                print(f"=== {path} ({len(sus)}/{total}) ===")
                for lineno, line in sus[:5]:
                    print(f"  L{lineno}: {line[:120]}")
                if len(sus) > 5:
                    print(f"  ... and {len(sus) - 5} more")
                print()
        if fix or clean_mode:
            parts: list[str] = []
            if fix:
                parts.append("{} runs {}".format(grand_fix, "would be fixed" if dry_run else "fixed"))
            if clean_mode:
                parts.append("{} U+FFFD {}".format(grand_clean, "would be cleaned" if dry_run else "cleaned"))
            print("\nDone. " + ", ".join(parts) + ".")
        return True

    @staticmethod
    def _file_has_fffd(path: str) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return "\ufffd" in f.read()
        except (OSError, UnicodeDecodeError):
            return False

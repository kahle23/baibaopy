"""
对象存储操作命令 - 提供对象的上传/下载/列举/删除/复制等子命令。
"""

import argparse
import json
import sys
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.oss import oss_mgr

log = logutil.getLogger(__name__)


class OssCommand(Command):
    """
    对象存储操作命令。

    支持子命令：
    - list: 列出已注册的存储实例名称
    - ls:   列举指定前缀下的对象键
    - put:  上传对象（来自文件、内联文本或 stdin）
    - get:  下载对象到本地文件或打印文本内容
    - cat:  打印对象文本内容
    - rm:   删除对象（幂等）
    - cp:   复制对象（支持跨桶）
    - mv:   移动对象（支持跨桶）
    - stat: 查看对象元信息
    - url:  生成对象临时访问 URL
    """

    @property
    def name(self) -> str:
        return "oss"

    @property
    def abbr(self) -> str:
        return "o"

    @property
    def description(self) -> str:
        return "对象存储操作（上传/下载/列举/删除/复制）"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao oss <子命令> [选项]\n"
            "\n"
            "子命令:\n"
            "  list                                    列出已注册的存储实例名称\n"
            "  ls     [PREFIX] [--inst NAME]           列举指定前缀下的对象键\n"
            "  put    KEY --file PATH | --text TEXT [--content-type T] [--meta K=V ...]\n"
            "                                          上传对象（--file - 读 stdin，可带元数据）\n"
            "  get    KEY [--out PATH] [--inst NAME]   下载对象（无 --out 时按文本打印）\n"
            "  cat    KEY [--inst NAME]                打印对象文本内容\n"
            "  rm     KEY [KEY...] [--inst NAME]       删除对象（幂等）\n"
            "  cp     SRC DST [--inst NAME]            复制对象（可跨桶：--bucket/--dst-bucket）\n"
            "  mv     SRC DST [--inst NAME]            移动对象（可跨桶：--bucket/--dst-bucket）\n"
            "  stat   KEY [--inst NAME]                查看对象元信息\n"
            "  url    KEY [--expires N] [--inst NAME]  生成临时访问 URL\n"
            "\n"
            "选项:\n"
            "  --inst        NAME  指定存储实例名（默认: default，实例经 .baibao/oss.config 注册）\n"
            "  --bucket      B     源桶名（默认: 实例配置的默认桶；除 list 外的子命令均支持）\n"
            "  --dst-bucket  DB    目标桶名，仅 cp/mv（默认: 同源桶，即同桶操作）\n"
            "  --expires     N     URL 有效期秒数（默认: 3600）\n"
            "  -h, --help          显示帮助信息"
        )

    def execute(self, ctx: CliContext) -> Any:
        args = ctx.current_args
        if not args:
            self.show_usage()
            return False

        subcommand = args[0]
        subcommand_args = args[1:]

        dispatch = {
            "list": self._list_instances,
            "ls": self._ls,
            "put": self._put,
            "get": self._get,
            "cat": self._cat,
            "rm": self._rm,
            "cp": self._cp,
            "mv": self._mv,
            "stat": self._stat,
            "url": self._url,
        }
        handler = dispatch.get(subcommand)
        if handler is None:
            if subcommand in ("-h", "--help"):
                self.show_usage()
                return True
            log.error(f"未知子命令: {subcommand}")
            self.show_usage()
            return False
        return handler(ctx, subcommand_args)

    # region ======== 子命令实现 ========
    def _list_instances(self, ctx: CliContext, args: list[str]) -> bool:
        """列出已注册的存储实例名称。"""
        loader = oss_mgr.get_config_loader()
        if loader:
            loader(oss_mgr, "")
        names = oss_mgr.get_registered_names()
        if not names:
            log.info("没有已注册的存储实例")
            return True

        ctx.print_delim()
        print("已注册的存储实例:")
        for name in sorted(names):
            client = oss_mgr.get_client(name)
            print(f"  - {name} ({client.oss_type})")
        ctx.print_delim()
        return True

    def _ls(self, ctx: CliContext, args: list[str]) -> bool:
        """列举指定前缀下的对象键。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss ls", description="列举对象键")
        parser.add_argument("prefix", nargs="?", default="", help="键前缀（默认全部）")
        parser.add_argument("--bucket", default=None, help="桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        stats = oss_mgr.list_objects(ns.prefix, bucket=ns.bucket, name=ns.inst)
        ctx.print_delim()
        for st in stats:
            size = st.size if st.size >= 0 else "?"
            print(f"{st.key}\t{size}")
        log.info("共 %d 个对象", len(stats))
        ctx.print_delim()
        return True

    def _put(self, ctx: CliContext, args: list[str]) -> bool:
        """上传对象（文件/文本/stdin 三选一，可带 content_type 与元数据）。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss put", description="上传对象")
        parser.add_argument("key", help="对象键")
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--file", default=None,
                            help="本地源文件路径（传 - 读 stdin；适合二进制/含特殊字符内容）")
        source.add_argument("--text", default=None, help="内联文本内容（UTF-8）")
        parser.add_argument("--content-type", dest="content_type", default=None,
                            help="对象 MIME 类型（省略时走文件级上传按扩展名自动推导，"
                                 "文本/stdin 上传不推导）")
        parser.add_argument("--meta", action="append", default=[], metavar="K=V",
                            help="用户自定义元数据，格式 键=值（可重复）")
        parser.add_argument("--bucket", default=None, help="桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            metadata = self._parse_meta(ns.meta)
            if ns.file is not None:
                if ns.file == "-":
                    data = sys.stdin.buffer.read()
                    oss_mgr.get_client(ns.inst).put_object(
                        ns.key, data, content_type=ns.content_type, metadata=metadata,
                        bucket=ns.bucket)
                else:
                    oss_mgr.upload_file(ns.key, ns.file, content_type=ns.content_type,
                                        metadata=metadata, bucket=ns.bucket, name=ns.inst)
                log.info("上传成功: %s <- %s", ns.key, ns.file)
            else:
                oss_mgr.put_object(ns.key, ns.text or "", content_type=ns.content_type,
                                   metadata=metadata, bucket=ns.bucket, name=ns.inst)
                log.info("上传成功: %s", ns.key)
            return True
        except Exception as e:
            log.error("上传失败: %s", e)
            return False

    @staticmethod
    def _parse_meta(pairs: list[str]) -> dict[str, str]:
        """解析 ``--meta 键=值`` 列表为字典（重复键取最后一个）。"""
        result: dict[str, str] = {}
        for pair in pairs:
            key, sep, value = pair.partition('=')
            if not sep or not key.strip():
                raise ValueError(f"--meta 格式应为 键=值，收到: {pair!r}")
            result[key.strip()] = value
        return result

    def _get(self, ctx: CliContext, args: list[str]) -> bool:
        """下载对象到本地文件，无 --out 时按文本打印。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss get", description="下载对象")
        parser.add_argument("key", help="对象键")
        parser.add_argument("--out", default=None, help="本地目标文件路径（省略时按文本打印）")
        parser.add_argument("--bucket", default=None, help="桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            if ns.out:
                oss_mgr.download_file(ns.key, ns.out, bucket=ns.bucket, name=ns.inst)
                log.info("下载成功: %s -> %s", ns.key, ns.out)
            else:
                ctx.print_delim()
                print(oss_mgr.get_object_text(ns.key, bucket=ns.bucket, name=ns.inst))
                ctx.print_delim()
            return True
        except Exception as e:
            log.error("下载失败: %s", e)
            return False

    def _cat(self, ctx: CliContext, args: list[str]) -> bool:
        """打印对象文本内容。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss cat", description="打印对象文本")
        parser.add_argument("key", help="对象键")
        parser.add_argument("--bucket", default=None, help="桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            ctx.print_delim()
            print(oss_mgr.get_object_text(ns.key, bucket=ns.bucket, name=ns.inst))
            ctx.print_delim()
            return True
        except Exception as e:
            log.error("读取失败: %s", e)
            return False

    def _rm(self, ctx: CliContext, args: list[str]) -> bool:
        """删除对象（幂等，支持多个键）。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss rm", description="删除对象")
        parser.add_argument("keys", nargs="+", help="对象键（可多个）")
        parser.add_argument("--bucket", default=None, help="桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            for key in ns.keys:
                oss_mgr.delete_object(key, bucket=ns.bucket, name=ns.inst)
                log.info("已删除: %s", key)
            return True
        except Exception as e:
            log.error("删除失败: %s", e)
            return False

    def _cp(self, ctx: CliContext, args: list[str]) -> bool:
        """复制对象。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss cp", description="复制对象")
        parser.add_argument("src", help="源对象键")
        parser.add_argument("dst", help="目标对象键")
        parser.add_argument("--bucket", default=None, help="源桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--dst-bucket", dest="dst_bucket", default=None,
                            help="目标桶名（默认: 同源桶，即同桶复制）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            oss_mgr.copy_object(ns.src, ns.dst, bucket=ns.bucket, dst_bucket=ns.dst_bucket,
                                name=ns.inst)
            log.info("复制成功: %s -> %s", ns.src, ns.dst)
            return True
        except Exception as e:
            log.error("复制失败: %s", e)
            return False

    def _mv(self, ctx: CliContext, args: list[str]) -> bool:
        """移动对象。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss mv", description="移动对象")
        parser.add_argument("src", help="源对象键")
        parser.add_argument("dst", help="目标对象键")
        parser.add_argument("--bucket", default=None, help="源桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--dst-bucket", dest="dst_bucket", default=None,
                            help="目标桶名（默认: 同源桶，即同桶移动）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            oss_mgr.move_object(ns.src, ns.dst, bucket=ns.bucket, dst_bucket=ns.dst_bucket,
                                name=ns.inst)
            log.info("移动成功: %s -> %s", ns.src, ns.dst)
            return True
        except Exception as e:
            log.error("移动失败: %s", e)
            return False

    def _stat(self, ctx: CliContext, args: list[str]) -> bool:
        """查看对象元信息。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss stat", description="对象元信息")
        parser.add_argument("key", help="对象键")
        parser.add_argument("--bucket", default=None, help="桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            st = oss_mgr.stat(ns.key, bucket=ns.bucket, name=ns.inst)
            if st is None:
                log.error("对象不存在: %s", ns.key)
                return False
            ctx.print_delim()
            print(json.dumps({
                "key": st.key,
                "size": st.size,
                "last_modified": st.last_modified.isoformat() if st.last_modified else None,
                "etag": st.etag,
                "content_type": st.content_type,
                "metadata": st.metadata,
            }, ensure_ascii=False, indent=2))
            ctx.print_delim()
            return True
        except Exception as e:
            log.error("查询失败: %s", e)
            return False

    def _url(self, ctx: CliContext, args: list[str]) -> bool:
        """生成对象临时访问 URL。"""
        parser = argparse.ArgumentParser(prog="python -m baibao oss url",
                                         description="生成临时访问 URL")
        parser.add_argument("key", help="对象键")
        parser.add_argument("--expires", type=int, default=3600, help="有效期秒数（默认: 3600）")
        parser.add_argument("--bucket", default=None, help="桶名（默认: 实例配置的默认桶）")
        parser.add_argument("--inst", default=None, help="存储实例名（默认: default）")
        ns = parser.parse_args(args)

        try:
            ctx.print_delim()
            print(oss_mgr.presigned_url(ns.key, expires=ns.expires, bucket=ns.bucket,
                                        name=ns.inst))
            ctx.print_delim()
            return True
        except Exception as e:
            log.error("生成 URL 失败: %s", e)
            return False
    # endregion

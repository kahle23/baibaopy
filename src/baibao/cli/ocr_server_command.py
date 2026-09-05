"""
OCR HTTP 服务命令 - 启动常驻内存的 OCR 服务。

把 ``baibao.ai.ocr`` 的多种引擎（RapidOCR / EasyOCR / PaddleOCR 2.x/3.x）暴露为统一的 HTTP
接口：模型在启动时按 ``--engines`` 预加载一次并驻留内存，避免每次 OCR 都重新
加载模型。请求时通过 ``engine`` 参数选择已加载的引擎，从而「一个接口走多个实现」。

使用方式：
    python -m baibao ocr_server                                       默认加载 rapid（轻量本地）
    python -m baibao ocr_server --engines rapid,paddle3               同时加载两个引擎
    python -m baibao ocr_server --engines paddle --lang en            PaddleOCR + 英文
    python -m baibao ocr_server --engines paddle --gpu --port 9000    GPU + 自定义端口

调用示例（服务启动后）：
    # 1) multipart 上传文件（最常用）
    curl -F "image=@shot.png" -F "engine=paddle" http://127.0.0.1:8000/ocr

    # 2) JSON base64（适合跨机器调用）
    curl -H "Content-Type: application/json" \\
         -d "{\"image_base64\":\"$(base64 -w0 shot.png)\",\"details\":true}" \\
         http://127.0.0.1:8000/ocr

    # 3) JSON 本地路径（仅适合服务所在机器，省去上传开销）
    curl -H "Content-Type: application/json" \\
         -d "{\"image_path\":\"C:/shots/shot.png\"}" http://127.0.0.1:8000/ocr
"""

import argparse
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.system import pip
from pykunlun.util import logutil

from baibao.ai.ocr import OcrCfg

log = logutil.getLogger(__name__)


class OcrServerCommand(Command):
    """
    OCR HTTP 服务命令。

    启动一个常驻内存的 HTTP 服务，预加载 ``--engines`` 指定的引擎；后续每个请求
    直接复用内存中的模型实例，避免重复加载（首次加载动辄数秒到数十秒）。请求经
    统一的 ``POST /ocr`` 接口，按 ``engine`` 参数路由到已加载的引擎——未在启动时
    加载的引擎会被拒绝，从而尊重「哪些模型加载、哪些不加载」的配置意图。
    """

    @property
    def name(self) -> str:
        return "ocr_server"

    @property
    def abbr(self) -> str:
        return "ocs"

    @property
    def description(self) -> str:
        return "启动常驻内存的 OCR HTTP 服务（一次加载，多次复用）"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao ocr_server [选项]"
            "  （引擎: rapid, easy, paddle, paddle2, paddle3；默认: rapid）\n"
            "\n"
            "选项:\n"
            "      --host HOST            监听地址（默认: 127.0.0.1）\n"
            "  -p, --port PORT            监听端口（默认: 8000；0=由系统分配空闲端口）\n"
            "      --engines LIST         预加载引擎，逗号分隔（默认: rapid；如 rapid,easy,paddle,paddle2,paddle3）\n"
            "      --lang CODE            识别语言，作用于所有预加载引擎（默认: ch；如 en、japan、ko、ch_tra）\n"
            "      --gpu                  启用 GPU（easy 需 CUDA 版 torch；paddle 需 paddlepaddle-gpu；rapid 恒 CPU）\n"
            "      --cpu-threads N        CPU 推理线程数（仅 paddle 引擎生效）\n"
            "      --no-angle-cls         关闭角度/方向分类（更快，但倾斜文本识别变差）\n"
            "      --default-engine NAME  默认引擎（请求未指定 engine 时使用；默认取 --engines 的第一个）\n"
            "      --max-image-mb N       单次请求图片大小上限（默认: 16 MB）\n"
            "  -h, --help                  显示帮助信息\n"
            "\n"
            "示例:\n"
            "  python -m baibao ocr_server\n"
            "  python -m baibao ocr_server --engines rapid,paddle3\n"
            "  python -m baibao ocr_server --engines paddle --lang en --gpu --port 9000\n"
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
            '--host',
            default='127.0.0.1',
            help='监听地址（默认: 127.0.0.1）',
        )
        parser.add_argument(
            '-p', '--port',
            type=int,
            default=8000,
            help='监听端口（默认: 8000；传 0 由系统分配空闲端口）',
        )
        parser.add_argument(
            '--engines',
            default='rapid',
            help='预加载引擎，逗号分隔（默认: rapid；可选 rapid,easy,paddle,paddle2,paddle3）',
        )
        parser.add_argument(
            '--lang',
            default='ch',
            help='识别语言代码，作用于所有预加载引擎（默认 ch 中英；如 en、japan、ko、ch_tra）',
        )
        parser.add_argument(
            '--gpu',
            action='store_true',
            help='启用 GPU（easy 需 CUDA 版 torch；paddle 需 paddlepaddle-gpu）',
        )
        parser.add_argument(
            '--cpu-threads',
            type=int,
            default=None,
            help='CPU 推理线程数（仅 paddle 引擎生效）',
        )
        parser.add_argument(
            '--no-angle-cls',
            action='store_true',
            help='关闭角度/方向分类（更快，但倾斜文本识别变差）',
        )
        parser.add_argument(
            '--default-engine',
            default=None,
            help='默认引擎（请求未指定 engine 时使用；默认取 --engines 的第一个）',
        )
        parser.add_argument(
            '--max-image-mb',
            type=int,
            default=16,
            help='单次请求图片大小上限，单位 MB（默认: 16）',
        )
        return parser.parse_args(args)

    # endregion

    # region ======== 执行入口 ========

    def execute(self, ctx: CliContext) -> Any:
        """
        启动 OCR HTTP 服务。

        Returns:
            True 表示正常退出（Ctrl+C）；False 表示参数错误或引擎加载失败。
        """
        args = ctx.current_args
        # 延迟导入 server 模块：避免 baibao help 等无关命令也连带加载 bottle。
        # bottle 是 server 的唯一直接依赖，未安装时自动安装（与 EasyOcr/PaddleOcr
        # 的自动安装策略一致，保证「开箱即用」）。
        try:
            import bottle  # noqa: F401  # pyright: ignore[reportUnusedImport]
        except ImportError:
            success, msg = pip.install('bottle')
            if not success:
                log.error(f"bottle 库未安装，自动安装失败: {msg}\n请手动运行: pip install bottle")
                return False

        from baibao.ai.ocr.server import (
            ENGINE_TYPES,
            preload_engines,
            run_server,
        )

        try:
            ns = self._parse_args(args)
        except SystemExit:
            return False

        # ---- 1) 校验引擎列表 ----
        engines = [e.strip() for e in ns.engines.split(',') if e.strip()]
        if not engines:
            log.error("--engines 不能为空")
            return False
        unknown = [e for e in engines if e not in ENGINE_TYPES]
        if unknown:
            log.error(f"未知引擎: {unknown}（可选: {list(ENGINE_TYPES)}）")
            return False
        if ns.default_engine and ns.default_engine not in engines:
            log.error(
                f"--default-engine '{ns.default_engine}' 未在 --engines 中列出: {engines}"
            )
            return False
        if ns.max_image_mb < 1:
            log.error(f"--max-image-mb 必须 ≥ 1，实际: {ns.max_image_mb}")
            return False

        # ---- 2) 预加载引擎（耗时操作）----
        options = OcrCfg(
            lang=ns.lang,
            gpu=ns.gpu,
            cpu_threads=ns.cpu_threads,
            use_angle_cls=not ns.no_angle_cls,
        )
        log.info("准备加载引擎: %s", engines)
        try:
            registry = preload_engines(engines, options, default=ns.default_engine)
        except Exception as e:
            log.error("加载 OCR 引擎失败: %s", e)
            return False

        # ---- 3) 启动 HTTP 服务（阻塞，直到 Ctrl+C）----
        run_server(
            registry,
            host=ns.host,
            port=ns.port,
            max_image_mb=ns.max_image_mb,
        )
        return True

    # endregion

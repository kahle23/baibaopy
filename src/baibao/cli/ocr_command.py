"""
OCR 命令 - 识别图片中的文字。

提供 baibao.ai.ocr 的命令行入口，支持在 EasyOCR 与 PaddleOCR 之间切换，
默认输出纯文本，便于脚本与无视觉能力的模型直接消费识别结果。

使用方式：
    python -m baibao ocr <图片路径>                                  默认 rapid 引擎（轻量本地）
    python -m baibao ocr <图片路径> --engine paddle                  PaddleOCR（中文精度更高）
    python -m baibao ocr <图片路径> --engine paddle --lang en         英文识别
    python -m baibao ocr <图片路径> --engine paddle --cpu-threads 8   多线程 CPU 推理
    python -m baibao ocr <图片路径> --details                         输出含坐标/置信度的 JSON
    python -m baibao ocr <图片路径> --delim __OCR__                   用分隔符包裹结果，便于精准截取
    python -m baibao ocr <图片路径> --draw out.png                    将识别框绘制到图片另存
"""

import argparse
import json
import os
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

from baibao.ai.ocr import OcrCfg, OcrEngine, OcrResult, build_ocr_engine

log = logutil.getLogger(__name__)

# 支持的引擎类型。引擎构造与参数映射统一收敛在 build_ocr_engine，
# 本命令只负责解析参数、组装 OcrCfg、调用工厂。
# - rapid   : RapidOCR（命令默认）：rapidocr + onnxruntime，约 30MB 内置 PP-OCRv6
#             small 模型，恒 CPU、中英文，免 PaddlePaddle/torch、运行时零联网
# - easy    : EasyOCR（多语言，torch 系，首次需下载约 1GB 模型）
# - paddle  : PaddleOcr 自动分发器（按已装 paddleocr 版本选 V2/V3，中文精度更高）
# - paddle2 : 显式 PaddleOcrV2（paddleocr 2.x API）
# - paddle3 : 显式 PaddleOcrV3（paddleocr 3.x API）
# - server  : 不加载本地模型，转发给运行中的 baibao ocr_server（客户端零重依赖）
_ENGINE_TYPES = ('rapid', 'easy', 'paddle', 'paddle2', 'paddle3', 'server')
DEFAULT_ENGINE = "rapid"


class OcrCommand(Command):
    """
    图片文字识别（OCR）命令。

    默认 RapidOCR（轻量本地：模型内置、恒 CPU、中英文），可选 EasyOCR（多语言）、
    PaddleOCR（中文精度更高）与 server（转发常驻 ocr_server）等引擎。
    默认向 stdout 输出纯文本，便于模型/脚本直接消费；加 ``--details`` 输出
    含坐标与置信度的 JSON；加 ``--draw`` 将识别框绘制到图片并另存。
    日志信息走 stderr，识别文字走 stdout，二者分离。
    """

    @property
    def name(self) -> str:
        return "ocr"

    @property
    def description(self) -> str:
        return "识别图片中的文字（OCR，支持 rapid/easy/paddle/server 引擎）"

    @property
    def usage(self) -> str:
        engines = ", ".join(_ENGINE_TYPES)
        choices = ",".join(_ENGINE_TYPES)
        return (
            f"python -m baibao ocr <图片路径> [选项]  （引擎: {engines}，默认: {DEFAULT_ENGINE}）\n"
            "\n"
            "选项:\n"
            f"  -e, --engine {{{choices}}}  OCR 引擎（默认: {DEFAULT_ENGINE}）\n"
            "      --lang CODE            识别语言（默认 ch 中英；如 en、japan、ko、ch_tra）\n"
            "      --gpu                  启用 GPU（需已装 GPU 版依赖）\n"
            "      --cpu-threads N        CPU 推理线程数（仅 paddle 引擎生效）\n"
            "      --server-url URL       server 引擎的 ocr_server 地址"
            "（默认 http://127.0.0.1:8000；或环境变量 BAIBAO_OCR_SERVER_URL）\n"
            "      --server-engine NAME   server 引擎要求服务端使用的引擎"
            "（如 paddle3；不填用服务端默认）\n"
            "  -d, --details               输出含坐标与置信度的 JSON 详情\n"
            "      --delim STR            结果分隔符：设则用其在 stdout 结果前后各占一行包裹，\n"
            "                              便于在夹杂日志时精准截取（由调用方保证唯一）\n"
            "      --draw PATH            将识别框绘制到图片并保存到 PATH\n"
            "  -h, --help                  显示帮助信息\n"
            "\n"
            "示例:\n"
            "  python -m baibao ocr shot.png\n"
            "  python -m baibao ocr shot.png --engine paddle --lang en\n"
            "  python -m baibao ocr shot.png --engine paddle --cpu-threads 8\n"
            "  python -m baibao ocr shot.png --engine paddle --delim __OCR__ 2>$null\n"
            "  python -m baibao ocr shot.png --details --draw out.png\n"
            "  python -m baibao ocr shot.png --engine server --server-engine paddle3\n"
        )

    # region ======== 参数解析 ========

    def _parse_args(self, args: list[str]) -> argparse.Namespace:
        """解析命令行参数。"""
        parser = argparse.ArgumentParser(
            prog=f"python -m baibao {self.name}",
            description=self.description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument("image", help="图片文件路径")
        parser.add_argument(
            "-e", "--engine",
            choices=list(_ENGINE_TYPES),
            default=DEFAULT_ENGINE,
            help=f"OCR 引擎（默认: {DEFAULT_ENGINE}）",
        )
        parser.add_argument(
            "--lang",
            default="ch",
            help="识别语言代码（默认 ch 中英；如 en 英文、japan 日文、ko 韩文、ch_tra 繁中）",
        )
        parser.add_argument(
            "--gpu",
            action="store_true",
            help="启用 GPU（easyocr 需 CUDA 版 torch；paddle 需 paddlepaddle-gpu；rapid 恒 CPU 不支持）",
        )
        parser.add_argument(
            "--cpu-threads",
            type=int,
            default=None,
            help="CPU 推理线程数（仅 paddle 引擎生效，加快 CPU 推理）",
        )
        parser.add_argument(
            "--server-url",
            default=None,
            help="server 引擎的 ocr_server 地址"
            "（默认 http://127.0.0.1:8000；或环境变量 BAIBAO_OCR_SERVER_URL）",
        )
        parser.add_argument(
            "--server-engine",
            default=None,
            help="server 引擎要求服务端使用的引擎（如 paddle3；不填用服务端默认）",
        )
        parser.add_argument(
            "-d", "--details",
            action="store_true",
            help="输出含坐标与置信度的 JSON 详情",
        )
        parser.add_argument(
            "--draw",
            metavar="PATH",
            help="将识别框绘制到图片并保存到 PATH",
        )
        return parser.parse_args(args)

    # endregion

    # region ======== 输出 / 引擎构造 ========

    def _build_engine(
        self,
        engine_type: str,
        options: OcrCfg,
        *,
        server_url: str | None = None,
        server_engine: str | None = None,
    ) -> OcrEngine:
        """
        按引擎类型 + 统一选项构造 OcrEngine。

        引擎间的参数差异（语言码、gpu/device、cpu_threads、角度分类）由
        :func:`build_ocr_engine` 统一映射，本命令不感知各引擎的构造细节。
        ``server`` 引擎的专属连接参数（地址 / 服务端引擎）单独透传。
        """
        return build_ocr_engine(
            engine_type, options, server_url=server_url, server_engine=server_engine
        )

    @staticmethod
    def _result_to_dict(r: OcrResult) -> dict[str, Any]:
        """将 OcrResult 转为可 JSON 序列化的字典。"""
        return {
            "text": r.text,
            "confidence": round(r.confidence, 4),
            "bbox": r.bbox,
        }

    # endregion

    # region ======== 执行入口 ========

    def execute(self, ctx: CliContext) -> Any:
        """
        执行 OCR 识别命令。

        Returns:
            True 表示识别成功；False 表示参数错误、文件缺失或识别异常。
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

        if not os.path.exists(ns.image):
            log.error(f"图片文件不存在: {ns.image}")
            _emit("(OCR 失败：图片不存在；详见 stderr)")
            return False

        options = OcrCfg(
            lang=ns.lang,
            gpu=ns.gpu,
            cpu_threads=ns.cpu_threads,
        )
        try:
            engine = self._build_engine(
                ns.engine,
                options,
                server_url=ns.server_url,
                server_engine=ns.server_engine,
            )
        except Exception as e:
            log.error(f"初始化 OCR 引擎失败: {e}")
            _emit("(OCR 失败：引擎初始化失败；详见 stderr)")
            return False

        try:
            if ns.details:
                results = engine.recognize_with_details(ns.image)
                # 紧凑输出：每个识别项独占一行（text + 置信度 + 四点框），
                # 相比 indent=2 大幅减少行数与 token，便于 AI 逐行消费；
                # 人类如需美化可自行 json.dumps(indent=2) 重排。
                items = [
                    json.dumps(self._result_to_dict(r), ensure_ascii=False, separators=(',', ':'))
                    for r in results
                ]
                payload = "[\n  " + ",\n  ".join(items) + "\n]" if items else "[]"
                _emit(payload)
            else:
                text = engine.recognize(ns.image)
                # 无识别内容时给明确标记，避免空输出被误判为命令失败
                _emit(text if text.strip() else "(OCR 未识别到任何文字)")

            if ns.draw:
                parent = os.path.dirname(os.path.abspath(ns.draw))
                os.makedirs(parent, exist_ok=True)
                engine.recognize_and_draw(ns.image, output_path=ns.draw)
                log.info(f"已保存标注图: {ns.draw}")

        except Exception as e:
            # 详细错误走 stderr（log.error）；同时在 stdout 留一个明确失败标记，
            # 这样调用方即使丢弃了 stderr（如 PowerShell 的 2>$null）也能立刻分辨
            # "识别到空" 与 "出错了"，而不是面对一个空输出。
            log.error(f"OCR 识别失败: {e}")
            _emit("(OCR 失败：详见 stderr 日志；若命令带了 2>$null，去掉它即可看到报错)")
            return False

        return True

    # endregion

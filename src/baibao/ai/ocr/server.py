"""
OCR HTTP 服务模块。

把 :mod:`baibao.ai.ocr` 的多种引擎（RapidOCR / EasyOCR / PaddleOCR 2.x/3.x）包装为常驻内存
的 HTTP 服务：模型在启动时按 ``--engines`` 预加载一次，后续请求直接复用内存中的
实例，避免每次 OCR 都重新加载模型（首次加载动辄数秒到数十秒）。

设计要点：
    - 一个统一的 ``POST /ocr`` 接口，请求时通过 ``engine`` 参数选择已加载的引擎，
      从而「一个接口走多个实现」；
    - 启动时通过 ``--engines`` 控制加载哪些引擎，未加载的引擎一律拒绝服务，
      从而尊重「哪些模型加载、哪些不加载」的显式配置；
    - 每个引擎实例配一把独立的锁串行化推理调用，规避底层库
      （EasyOCR / PaddleOCR）并发访问同一模型实例时的内部状态竞争；
    - HTTP 层用 bottle（轻量、单文件），配合标准库 ``wsgiref`` + ``ThreadingMixIn``
      提供多线程——不同引擎的请求可真正并行，同引擎请求串行排队。

接口一览：
    GET  /          服务状态与已加载引擎
    GET  /engines   已加载引擎列表
    POST /ocr       执行 OCR 识别（multipart 文件上传 或 JSON base64 / 本地路径）

注意：
    本模块不在顶部导入 bottle，仅在 :func:`build_app` 内按需导入（类型注解走
    ``TYPE_CHECKING``，运行时零成本）。调用方（含 CLI 命令）可直接 import 本模块，
    不会连带加载 bottle。
"""

import base64
import json
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pykunlun.ai.ocr import OcrCfg, OcrEngine, OcrResult
from pykunlun.util import logutil

from . import build_ocr_engine

if TYPE_CHECKING:
    import bottle
    import numpy as np

log = logutil.getLogger(__name__)


# region ======== 常量 ========

# 支持的引擎类型（与 baibao.ai.ocr.build_ocr_engine 对齐）。
ENGINE_TYPES: tuple[str, ...] = ('rapid', 'easy', 'paddle', 'paddle2', 'paddle3')

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8000
DEFAULT_ENGINES = 'rapid'
DEFAULT_MAX_IMAGE_MB = 16

# endregion


# region ======== 已加载引擎注册表 ========

@dataclass
class _LoadedEngine:
    """一个已加载的引擎实例及其专属推理锁。"""

    engine: OcrEngine
    lock: threading.Lock


class OcrEngineRegistry:
    """
    已加载 OCR 引擎的注册表（线程安全）。

    服务启动时按 ``--engines`` 预实例化各引擎的 :class:`OcrEngine` 并驻留内存。
    请求时按引擎类型取已加载实例；未加载的引擎会明确报错，从而尊重启动时的加载配置。
    每个引擎自带一把推理锁，调用方在调用 ``recognize*`` 前后加锁，串行化对同一
    模型实例的并发请求（不同引擎之间互不阻塞）。
    """

    def __init__(self) -> None:
        self._entries: dict[str, _LoadedEngine] = {}
        self._default: str | None = None
        self._lock = threading.Lock()

    def register(self, engine_type: str, engine: OcrEngine) -> None:
        """注册一个已实例化的引擎。"""
        with self._lock:
            self._entries[engine_type] = _LoadedEngine(
                engine=engine, lock=threading.Lock()
            )
            log.debug("已注册引擎: %s", engine_type)

    def set_default(self, engine_type: str) -> None:
        """设置默认引擎类型（请求未指定 engine_type 时使用）。"""
        with self._lock:
            if engine_type not in self._entries:
                raise ValueError(f"无法设置默认引擎：'{engine_type}' 未加载")
            self._default = engine_type

    def resolve(self, engine_type: str | None) -> tuple[str, _LoadedEngine]:
        """
        解析出实际使用的引擎类型及其已加载实例。

        Args:
            engine_type: 请求指定的引擎类型；为 ``None`` 时使用默认引擎。

        Returns:
            ``(engine_type, _LoadedEngine)`` 二元组。

        Raises:
            ValueError: 未加载任何引擎，或请求的引擎未在启动时加载。
        """
        with self._lock:
            name = engine_type or self._default
            if name is None:
                raise ValueError("尚未加载任何 OCR 引擎")
            loaded = self._entries.get(name)
            if loaded is None:
                available = list(self._entries)
                raise ValueError(f"引擎 '{name}' 未加载（可用: {available}）")
            return name, loaded

    def names(self) -> list[str]:
        """返回已加载引擎名列表（按注册顺序）。"""
        with self._lock:
            return list(self._entries)

    @property
    def default_name(self) -> str | None:
        """默认引擎名；未加载任何引擎时为 ``None``。"""
        with self._lock:
            return self._default


def preload_engines(
    engine_types: list[str],
    options: OcrCfg,
    default: str | None = None,
) -> OcrEngineRegistry:
    """
    按 ``engine_types`` 顺序预实例化各引擎并注册到新注册表。

    模型加载是耗时操作（EasyOCR 首次需下载约 1GB 模型；PaddleOCR 也需加载多个
    子模型），放在服务启动阶段一次性完成，后续请求零加载成本。

    Args:
        engine_types: 引擎类型列表，按顺序加载。每个引擎用同一份 ``options`` 构造
            （引擎间的参数差异由 :func:`build_ocr_engine` 内部映射）。
        options: 引擎无关的统一选项（lang/gpu/cpu_threads/use_angle_cls）。
        default: 默认引擎类型；为 ``None`` 时取 ``engine_types[0]``。

    Returns:
        已填入引擎实例的 :class:`OcrEngineRegistry`。

    Raises:
        ValueError: 未知引擎类型（透传自 :func:`build_ocr_engine`）。
        ImportError: 引擎依赖安装失败（透传自各引擎构造函数）。
    """
    registry = OcrEngineRegistry()
    for engine_type in engine_types:
        log.info(
            "正在加载 OCR 引擎: %s (lang=%s, gpu=%s, use_angle_cls=%s)",
            engine_type, options.lang, options.gpu, options.use_angle_cls,
        )
        engine = build_ocr_engine(engine_type, options)
        registry.register(engine_type, engine)
        log.info("✓ 引擎就绪: %s", engine_type)

    default_name = default or (engine_types[0] if engine_types else None)
    if default_name:
        registry.set_default(default_name)
    return registry


# endregion


# region ======== 图片解码 / 工具 ========

def _decode_image_bytes(raw: bytes) -> 'np.ndarray[Any, np.dtype[Any]]':
    """把原始图片字节解码为 OpenCV 图像数组（BGR）。"""
    import cv2
    import numpy as np

    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(
            "无法解码图片，请检查文件格式（支持 png/jpg/jpeg/bmp/webp 等）"
        )
    return img


def _check_size(raw: bytes, limit: int) -> None:
    """校验字节数据是否超过上限，超限抛 ValueError。"""
    if len(raw) > limit:
        mb = limit / (1024 * 1024)
        raise ValueError(f"图片过大: {len(raw)} 字节，超过上限约 {mb:.1f} MB")


def _parse_bool(val: object, default: bool = False) -> bool:
    """宽松解析布尔值，兼容 bool/int/str（如 'true'、'1'、'yes'）。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    return str(val).strip().lower() in ('1', 'true', 'yes', 'on')


def _result_to_dict(r: OcrResult) -> dict[str, Any]:
    """
    将 OcrResult 转为可 JSON 序列化的字典。

    bbox 的坐标可能来自底层引擎的 numpy 标量（如 np.int32 / np.float32），
    直接交由 bottle 的 json.dumps 会抛 ``... is not JSON serializable``。
    这里强制转成原生 Python int / float，作为 HTTP 出口的兜底清洗，
    避免不同引擎（paddle 2.x/3.x、easyocr）的数据类型差异泄漏到序列化层。
    """
    bbox = [[int(p[0]), int(p[1])] for p in (r.bbox or [])]
    return {
        "text": r.text,
        "confidence": round(float(r.confidence), 4),
        "bbox": bbox,
    }


# endregion


# region ======== HTTP 应用（bottle） ========

def build_app(registry: OcrEngineRegistry, max_image_bytes: int) -> 'bottle.Bottle':
    """
    构造 OCR HTTP 应用（:class:`bottle.Bottle`，本身是合法的 WSGI callable）。

    Args:
        registry: 已加载引擎的注册表。
        max_image_bytes: 单次请求图片字节上限，超限返回 413 / 400。
    """
    import bottle

    # bottle 无类型标注（项目不做 stubs）：在边界把 Unknown 收口为显式 Any——
    # reportUnknown* 只报 Unknown、不报显式 Any；结构化取值处仍按真实类型 cast。
    request: Any = bottle.request
    response: Any = bottle.response
    app: Any = bottle.Bottle()

    # bottle 默认 MEMFILE_MAX 仅 100KB，而 JSON(base64) 请求体动辄数 MB；
    # 不调高的话 bottle 在 .request.json 内部就会抛 413，根本到不了下面的
    # _check_size 精确校验。base64 膨胀约 4/3，加 JSON 外壳按 2 倍留余量。
    # （bottle 未标注 MEMFILE_MAX 可写，运行时它就是普通类属性，赋值合法）
    bottle.BaseRequest.MEMFILE_MAX = max_image_bytes * 2  # pyright: ignore[reportAttributeAccessIssue]

    # 每个请求进入/结束各打一条日志（方法、路径、客户端、状态、耗时），
    # 覆盖全部路由与全部返回路径（成功 / 400 / 413 / 500 / 异常）。
    def _on_request_start() -> None:
        request.environ['baibao.ocr.t0'] = time.perf_counter()
        log.info(
            "[http] --> %s %s client=%s ctype=%s len=%s",
            request.method,
            request.path,
            request.remote_addr,
            request.content_type or '',
            request.content_length or 0,
        )

    def _on_request_end() -> None:
        t0 = request.environ.get('baibao.ocr.t0')
        ms = (time.perf_counter() - t0) * 1000 if t0 is not None else -1
        log.info(
            "[http] <-- %s %s status=%s 耗时=%.1fms",
            request.method,
            request.path,
            response.status,
            ms,
        )

    app.add_hook('before_request', _on_request_start)
    app.add_hook('after_request', _on_request_end)

    def _err(status: int, message: str) -> dict[str, Any]:
        response.status = status
        response.content_type = 'application/json; charset=utf-8'
        return {'success': False, 'error': message}

    # bottle 无类型标注：route 装饰器内部的 load() 兜底让 pyright 把装饰器
    # 返回类型推断成含 ModuleType 的联合（报"not callable"），并连带"装饰器吞掉
    # 函数引用"的 UnusedFunction。三码同根因，路由注册处逐行豁免。
    @app.get('/')  # pyright: ignore[reportCallIssue, reportUntypedFunctionDecorator]
    def index() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        return {
            'service': 'baibao-ocr',
            'engines': registry.names(),
            'default': registry.default_name,
        }

    @app.get('/engines')  # pyright: ignore[reportCallIssue, reportUntypedFunctionDecorator]
    def engines() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        return {
            'engines': registry.names(),
            'default': registry.default_name,
        }

    @app.post('/ocr')  # pyright: ignore[reportCallIssue, reportUntypedFunctionDecorator]
    def ocr() -> Any:  # pyright: ignore[reportUnusedFunction]
        # ---- 1) 请求体总量预检（content-length 可得时）----
        # 预检只是「明显超限就早拒绝」的优化，精确校验由 _check_size 按解码后
        # 字节数完成。JSON(base64) 请求体比原图膨胀约 4/3，阈值取 2 倍以同时兼容
        # 两种上传方式（multipart 体积 ≈ 原图，JSON base64 ≈ 1.33×原图）。
        cl = request.content_length or 0
        if cl and cl > max_image_bytes * 2:
            mb = max_image_bytes / (1024 * 1024)
            return _err(413, f"请求体过大（{cl} 字节），超过上限约 {mb:.1f} MB")

        ctype = (request.content_type or '').lower()

        # ---- 2) 解析 engine / details / image ----
        details = False
        image_input: str | np.ndarray[Any, np.dtype[Any]]

        try:
            if ctype.startswith('application/json'):
                # bottle 的 json/forms/files 成员是 DictProperty 描述符（无类型标注），
                # 运行时分别是 dict / FormsDict，cast 到真实类型取值
                payload = cast("dict[str, Any]", request.json) or {}
                engine = payload.get('engine') or None
                details = _parse_bool(payload.get('details', False))

                image_path = payload.get('image_path')
                b64 = payload.get('image_base64')
                if image_path:
                    image_input = str(image_path)
                elif b64:
                    raw = base64.b64decode(b64)
                    _check_size(raw, max_image_bytes)
                    image_input = _decode_image_bytes(raw)
                else:
                    return _err(
                        400, "JSON 请求需提供 'image_base64' 或 'image_path'"
                    )
            else:
                # multipart/form-data 或 application/x-www-form-urlencoded
                # FormsDict 按 str→str 字典取值；FileUpload 仅用 .file 句柄，走 Any
                forms = cast("dict[str, str]", request.forms)
                engine = forms.get('engine') or None
                details = _parse_bool(forms.get('details', ''))

                files = cast("dict[str, Any]", request.files)
                upload = files.get('image')
                path = forms.get('image_path')
                if upload is not None and upload.file is not None:
                    raw = upload.file.read()
                    _check_size(raw, max_image_bytes)
                    image_input = _decode_image_bytes(raw)
                elif path:
                    image_input = str(path)
                else:
                    return _err(
                        400,
                        "请通过 multipart 上传 'image' 文件，或提供 'image_path'",
                    )
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            log.exception("解析 /ocr 请求失败")
            return _err(400, f"解析请求失败: {e}")

        # ---- 3) 解析引擎 ----
        try:
            engine_type, loaded = registry.resolve(engine)
        except ValueError as e:
            return _err(400, str(e))

        # ---- 4) 执行识别（加引擎锁，串行化同引擎并发）----
        try:
            with loaded.lock:
                if details:
                    results = loaded.engine.recognize_with_details(image_input)
                    detail_list = [_result_to_dict(r) for r in results]
                    text = '\n'.join(r.text for r in results)
                else:
                    text = loaded.engine.recognize(image_input)
                    detail_list = None
        except FileNotFoundError as e:
            return _err(404, str(e))
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            log.exception("OCR 识别失败")
            return _err(500, f"OCR 识别失败: {e}")

        resp: dict[str, Any] = {
            'success': True,
            'engine': engine_type,
            'text': text,
        }
        if detail_list is not None:
            resp['details'] = detail_list
        return resp

    # 统一 JSON 错误输出，便于客户端解析
    @app.error(404)  # pyright: ignore[reportCallIssue, reportUntypedFunctionDecorator]
    def _e404(error: Any) -> str:  # pyright: ignore[reportUnusedFunction]
        response.content_type = 'application/json; charset=utf-8'
        return json.dumps(
            {'success': False, 'error': f"路由不存在: {request.path}"},
            ensure_ascii=False,
        )

    @app.error(405)  # pyright: ignore[reportCallIssue, reportUntypedFunctionDecorator]
    def _e405(error: Any) -> str:  # pyright: ignore[reportUnusedFunction]
        response.content_type = 'application/json; charset=utf-8'
        return json.dumps(
            {'success': False, 'error': '请求方法不允许'}, ensure_ascii=False
        )

    @app.error(500)  # pyright: ignore[reportCallIssue, reportUntypedFunctionDecorator]
    def _e500(error: Any) -> str:  # pyright: ignore[reportUnusedFunction]
        response.content_type = 'application/json; charset=utf-8'
        msg = str(error.exception) if error.exception else '内部错误'
        return json.dumps(
            {'success': False, 'error': msg}, ensure_ascii=False
        )

    return app


# endregion


# region ======== 多线程 WSGI 服务器 ========

def run_server(
    registry: OcrEngineRegistry,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_image_mb: int = DEFAULT_MAX_IMAGE_MB,
) -> None:
    """
    启动多线程 WSGI 服务并阻塞，直到收到 Ctrl+C。

    使用标准库 ``wsgiref`` + ``socketserver.ThreadingMixIn`` 提供多线程，无需额外
    依赖；不同引擎的请求可并行处理，同引擎请求由注册表的锁串行化。

    Args:
        registry: 已加载引擎的注册表。
        host: 监听地址。绑定到 ``0.0.0.0`` 时需注意：``image_path`` 选项允许读取
            服务所在机器上的本地文件，对外暴露会带来任意文件读取风险。
        port: 监听端口；传 ``0`` 由操作系统分配空闲端口（实际端口会写入日志）。
        max_image_mb: 单次请求图片大小上限（MB）。
    """
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import (
        WSGIRequestHandler,
        WSGIServer,
        make_server,
    )

    class _ThreadingServer(ThreadingMixIn, WSGIServer):
        # 守护线程：主进程退出时立即回收工作线程，避免卡在长连接上
        daemon_threads = True

    class _QuietHandler(WSGIRequestHandler):
        # 屏蔽反向 DNS 解析，加速请求处理
        def address_string(self) -> str:
            return self.client_address[0]

        # 关闭默认 access log（每行请求日志），关键事件由 baibao 日志接管
        def log_message(self, format: str, *args: Any) -> None:
            pass

    max_image_bytes = max_image_mb * 1024 * 1024
    app = build_app(registry, max_image_bytes=max_image_bytes)

    httpd = make_server(host, port, app, _ThreadingServer, _QuietHandler)
    actual_port = httpd.server_port

    log.info("=" * 60)
    log.info("BaiBao OCR HTTP 服务已启动")
    log.info("监听: http://%s:%d", host, actual_port)
    log.info(
        "已加载引擎: %s（默认: %s）",
        registry.names(), registry.default_name,
    )
    log.info("接口:")
    log.info("  GET  /          服务状态与已加载引擎")
    log.info("  GET  /engines   已加载引擎")
    log.info(
        "  POST /ocr       OCR 识别"
        "（multipart 上传 image / JSON image_base64 / JSON image_path）"
    )
    log.info("按 Ctrl+C 停止服务")
    log.info("=" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断信号，正在关闭...")
    finally:
        httpd.server_close()


# endregion

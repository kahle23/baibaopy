"""
OCR「服务端代理」策略实现模块（基于 Python 标准库，无重依赖）。

与本地加载模型的 EasyOCR / PaddleOCR 不同，本策略不在本地加载任何模型，而是把图像
通过 HTTP 转发给一个运行中的 ``baibao ocr_server``（见 :mod:`baibao.ai.ocr.server`），
复用服务端常驻内存的模型实例完成识别。

适用场景：模型已在服务端一次性加载，多个客户端 / 多次命令行调用共享同一份模型，
避免每次都重新加载模型。客户端只依赖 Python 标准库（``urllib``），无需安装
easyocr / paddleocr / torch / paddlepaddle 等重依赖——只要输入是图片路径，
连 opencv / numpy 都不需要（图像解码在服务端完成）。

引擎类型（engine_type）：``server``。服务端地址 / 要求服务端使用的引擎 / 超时通过本类的构造参数
（``server_url`` / ``server_engine`` / ``timeout``）指定；``server_url`` 缺省时取环境变量
``BAIBAO_OCR_SERVER_URL``，再退回默认 ``http://127.0.0.1:8000``。这些 server 专属参数
不放入通用 :class:`OcrCfg`，避免污染引擎无关配置。
"""

import base64
import json
import os
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, cast

from pykunlun.ai.ocr import OcrCfg, OcrEngine, OcrResult
from pykunlun.util import logutil

if TYPE_CHECKING:
    import numpy as np

log = logutil.getLogger(__name__)


class ServerOcr(OcrEngine):
    """
    通过 ``baibao ocr_server`` HTTP 服务做 OCR 的策略实现。

    不在本地加载任何模型：把图像以 base64 POST 到服务端 ``/ocr``，由服务端已加载的
    引擎完成识别并返回结构化结果，客户端仅依赖标准库。

    重写了 :meth:`recognize` / :meth:`recognize_with_details`：当输入为图片路径或
    字节时，直接读取原始字节上送，**不经过 opencv / numpy**，从而让客户端真正零重依赖；
    仅当输入为 numpy 数组（或调用 :meth:`recognize_and_draw` 需要本地绘制）时才需要
    opencv（用于编码 / 绘制）。

    Note:
        本类的 :attr:`engine_type` 为类型标识 ``'server'``（继承自 :class:`OcrEngine`）；
        要求服务端使用的引擎名由构造参数 ``server_engine`` 表达，两者含义不同，注意区分。
    """

    engine_type = 'server'

    DEFAULT_URL = 'http://127.0.0.1:8000'
    DEFAULT_TIMEOUT = 60.0
    _ENV_URL = 'BAIBAO_OCR_SERVER_URL'

    # region ======== 构造与配置校验 ========
    def __init__(
        self,
        cfg: OcrCfg | None = None,
        *,
        server_url: str | None = None,
        server_engine: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Args:
            cfg: 通用 :class:`OcrCfg`（用于引擎一致性等；server 引擎不读取其中的
                ``lang`` / ``gpu`` 等本地推理字段）。为 ``None`` 时用默认配置。
            server_url: ``ocr_server`` 根地址；为 ``None`` 时取环境变量
                ``BAIBAO_OCR_SERVER_URL``，再退回 :attr:`DEFAULT_URL`。
            server_engine: 要求服务端使用的引擎名（如 ``paddle3``）；为 ``None`` 时用服务端默认。
            timeout: HTTP 请求超时秒数；为 ``None`` 时取 :attr:`DEFAULT_TIMEOUT`。
        """
        super().__init__(cfg if cfg is not None else OcrCfg(engine_type='server'))

        self._url: str = (
            server_url
            or os.environ.get(self._ENV_URL)
            or self.DEFAULT_URL
        ).rstrip('/')
        self._server_engine: str | None = server_engine
        self._timeout: float = timeout if timeout is not None else self.DEFAULT_TIMEOUT

    def _validate_and_prepare_cfg(self) -> None:
        """
        server 引擎的 cfg 校验：无通用字段需要补全。

        server 不使用本地 ``lang``（语言由服务端引擎决定），故覆盖基类以跳过 ``lang`` 非空校验；
        服务端连接参数（url / engine / timeout）由本类自身构造参数处理，不在 cfg 中。
        """
    # endregion

    # region ======== getter ========
    @property
    def server_url(self) -> str:
        """实际使用的服务端根地址。"""
        return self._url

    @property
    def server_engine(self) -> str | None:
        """要求服务端使用的引擎名（``None`` 表示用服务端默认）。"""
        return self._server_engine

    @property
    def timeout(self) -> float:
        """HTTP 请求超时秒数。"""
        return self._timeout
    # endregion

    # region ======== 图像 → base64 ========
    @staticmethod
    def _image_to_base64(image: object) -> str:
        """
        把任意合法输入转为 base64 字符串。

        - 字符串路径：直接读取原始字节（无需 opencv / numpy，解码在服务端完成）；
        - bytes / bytearray：原样编码；
        - numpy 数组：用 opencv 编码为 PNG（此分支需要本地安装 opencv）。
        """
        if isinstance(image, str):
            if not os.path.exists(image):
                raise FileNotFoundError(f"图片文件不存在: {image}")
            with open(image, 'rb') as f:
                return base64.b64encode(f.read()).decode('ascii')

        if isinstance(image, (bytes, bytearray)):
            return base64.b64encode(bytes(image)).decode('ascii')

        # 视为 numpy 数组：需要 opencv 编码
        import cv2

        ok, buf = cv2.imencode('.png', cast('np.ndarray[Any, np.dtype[Any]]', image))
        if not ok:
            raise RuntimeError('图像编码为 PNG 失败')
        return base64.b64encode(buf.tobytes()).decode('ascii')
    # endregion

    # region ======== HTTP 调用 ========
    def _post_ocr(self, image: object) -> dict[str, Any]:
        """
        把图像 POST 到服务端 ``/ocr``，返回完整响应 dict。

        始终带 ``details=true``，以便拿到每行的 bbox / confidence；纯文本由本类
        :meth:`recognize` 自行拼接。任何网络 / 服务端错误均转为 :class:`RuntimeError`。
        """
        b64 = self._image_to_base64(image)
        payload: dict[str, Any] = {'image_base64': b64, 'details': True}
        if self._server_engine:
            payload['engine'] = self._server_engine

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self._url + '/ocr',
            data=data,
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', errors='replace')
            raise RuntimeError(f"OCR 服务返回 HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"无法连接 OCR 服务 {self._url}: {e.reason}"
            ) from e

        result: dict[str, Any] = json.loads(body)
        return result

    @staticmethod
    def _details_to_results(details: list[Any]) -> list[OcrResult]:
        """把服务端返回的 details 列表映射为 :class:`OcrResult` 列表。"""
        return [
            OcrResult(
                text=d.get('text', ''),
                bbox=d.get('bbox') or [],
                confidence=float(d.get('confidence', 0.0)),
            )
            for d in details
        ]
    # endregion

    # region ======== OcrEngine 实现 ========
    def recognize(self, image: object) -> str:
        """
        识别图片中的文字，返回纯文本。

        重写基类：输入为路径 / 字节时不经过 opencv，直接上送服务端解码。
        """
        result = self._post_ocr(image)
        if not result.get('success'):
            raise RuntimeError(f"OCR 服务失败: {result.get('error')}")
        details: list[dict[str, Any]] = result.get('details') or []
        return '\n'.join(d.get('text', '') for d in details if d.get('text'))

    def recognize_with_details(self, image: object) -> list[OcrResult]:
        """
        识别图片中的文字，返回含位置与置信度的详细结果。

        重写基类：输入为路径 / 字节时不经过 opencv，直接上送服务端解码。
        """
        result = self._post_ocr(image)
        if not result.get('success'):
            raise RuntimeError(f"OCR 服务失败: {result.get('error')}")
        return self._details_to_results(result.get('details') or [])

    def _recognize_array(self, image: 'np.ndarray[Any, np.dtype[Any]]') -> list[OcrResult]:
        """
        仅供基类 :meth:`OcrEngine.recognize_and_draw` 走的模板方法入口。

        到这里的 ``image`` 必是经 :meth:`OcrEngine._load_image` 加载的 numpy 数组（绘制场景
        本就需要本地 opencv），转发给 :meth:`recognize_with_details` 复用上送逻辑。
        """
        return self.recognize_with_details(image)
    # endregion

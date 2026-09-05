"""
EasyOCR 策略实现模块。

基于 EasyOCR 库提供本地 OCR 能力，支持 80+ 种语言。
图片加载、文本清洗与结果绘制等通用流程由 :class:`OcrEngine` 基类统一处理，
本模块仅负责调用引擎并将结果映射为 :class:`OcrResult`。
"""

from typing import TYPE_CHECKING, Any

from pykunlun.ai.ocr import OcrCfg, OcrEngine, OcrResult
from pykunlun.system import pip

if TYPE_CHECKING:
    import numpy as np

# baibao 统一语言码 → EasyOCR 语言列表的映射。
# 供 EasyOcr 在构造时做"引擎无关 lang → EasyOCR langs"的转换。
_EASY_LANG_MAP: dict[str, list[str]] = {
    'ch': ['ch_sim', 'en'],       # 简体中文 + 英文
    'en': ['en'],
    'japan': ['ja', 'en'],
    'ko': ['ko', 'en'],
    'ch_tra': ['ch_tra', 'en'],   # 繁体中文
}


def langs_from_code(lang: str) -> list[str]:
    """
    把 baibao 统一语言码（如 ``'ch'``、``'en'``）映射为 EasyOCR 的语言列表。

    未知代码原样作为单元素列表返回，交给 EasyOCR 自行校验。
    """
    return _EASY_LANG_MAP.get(lang, [lang])


class EasyOcr(OcrEngine):
    """
    基于 EasyOCR 库的本地 OCR 策略实现。

    EasyOCR 是基于 PyTorch 的开源 OCR 库，支持 80+ 种语言。
    本实现提供完整的本地文字识别能力，无需联网即可运行。

    特性:
        - 支持多语言识别，默认支持简体中文和英文
        - 支持 GPU 加速（需 CUDA 环境）
        - 自动下载和管理模型文件（首次使用约 1GB）

    依赖:
        - easyocr: 核心 OCR 引擎
        - opencv-python: 图像处理
        - torch: 深度学习框架（easyocr 依赖）

    示例::

        from baibao.ai.ocr import EasyOcr
        from pykunlun.ai.ocr import OcrCfg

        # 默认配置（中文 + 英文）
        ocr = EasyOcr(OcrCfg())

        # 多语言 + GPU 加速
        ocr = EasyOcr(OcrCfg(lang='japan', gpu=True))
    """

    engine_type = 'easy'

    def __init__(self, cfg: OcrCfg) -> None:
        """
        初始化 EasyOCR 策略。

        先调基类构造（绑定并校验 cfg），再做引擎特定的重依赖加载。

        Args:
            cfg: OCR 配置。``lang`` 经 :func:`langs_from_code` 映射为 EasyOCR 语言列表
                （如 ``'ch'`` → ``['ch_sim', 'en']``）；``gpu`` 决定是否启用 GPU。

        Raises:
            ImportError: 当 easyocr 库未安装且自动安装失败时抛出。
        """
        super().__init__(cfg)

        try:
            import easyocr
        except ImportError:
            success, msg = pip.install('easyocr')
            if not success:
                raise ImportError(
                    f"easyocr 库未安装，自动安装失败: {msg}\n"
                    "请手动运行: pip install easyocr"
                )
            import easyocr

        self._langs: list[str] = langs_from_code(cfg.lang)
        self._gpu: bool = cfg.gpu
        # easyocr 无类型标注（项目不做 stubs）：实例收口为显式 Any——
        # reportUnknown* 只报 Unknown、不报显式 Any；返回结构在用到处按真实形状注解。
        self._reader: Any = easyocr.Reader(
            lang_list=self._langs,
            gpu=self._gpu,
        )

    @property
    def langs(self) -> list[str]:
        """获取当前配置的语言代码列表，如 ``['ch_sim', 'en']``。"""
        return self._langs

    @property
    def gpu_enabled(self) -> bool:
        """获取是否启用 GPU 加速。``True`` 表示启用，``False`` 表示使用 CPU。"""
        return self._gpu

    def _recognize_array(self, image: 'np.ndarray[Any, np.dtype[Any]]') -> list[OcrResult]:
        """
        调用 EasyOCR 识别图像数组。

        EasyOCR 的返回结构为 ``[(bbox, text, confidence), ...]``，
        这里仅做格式映射，文本清洗与空白过滤由基类统一处理。

        Args:
            image: OpenCV 图像数组（BGR 格式）。

        Returns:
            :class:`OcrResult` 对象列表。
        """
        result: list[tuple[list[tuple[int, int]], str, float]] = self._reader.readtext(image)
        return [
            OcrResult(
                text=text,
                bbox=[(int(p[0]), int(p[1])) for p in bbox],
                confidence=float(confidence),
            )
            for bbox, text, confidence in result
        ]

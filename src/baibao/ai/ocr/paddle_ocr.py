"""
PaddleOCR 策略实现模块。

提供三个对外类：

- :class:`PaddleOcrV2`：基于 paddleocr **2.x** API 的封装（``use_angle_cls``、``.ocr(cls=...)``）。
- :class:`PaddleOcrV3`：基于 paddleocr **3.x** API 的封装（``use_textline_orientation``、``.predict()``）。
- :class:`PaddleOcr`：自动分发器——按本地已安装的 paddleocr 版本委托给 V2 或 V3，
  调用方无需关心版本差异。未安装时自动安装（取最新版，当前即 3.x）。

之所以拆成 V2 / V3 两套实现：paddleocr 3.x 对 2.x 做了不兼容的 API 改造
（构造参数 ``use_angle_cls`` 被废弃、更名为 ``use_textline_orientation``；
``.ocr()`` 被 ``.predict()`` 取代；返回结构也完全不同），无法用一套代码同时兼容。

图片加载、文本清洗与结果绘制等通用流程由 :class:`OcrEngine` 基类统一处理，
本模块仅负责调用引擎并把结果映射为 :class:`OcrResult`。
"""

import dataclasses
from typing import TYPE_CHECKING, Any

from pykunlun.ai.ocr import OcrCfg, OcrEngine, OcrResult
from pykunlun.system import pip

if TYPE_CHECKING:
    import numpy as np

# region ======== 版本探测 ========

def get_paddleocr_version() -> str | None:
    """
    获取本地已安装的 paddleocr 版本号字符串（如 ``'2.7.0'``、``'3.0.0'``）。

    未安装、或已安装但导入失败（例如其依赖 paddlepaddle / paddlex / modelscope /
    torch 安装异常、DLL 加载失败）时返回 ``None``，不抛异常——供自动分发器据此
    决定是否需要安装、以及给出明确的错误提示。
    """
    try:
        import paddleocr
        return getattr(paddleocr, '__version__', None)
    except Exception:
        return None


def _major_version(version: str | None) -> int:
    """从版本号字符串中取主版本号；解析失败或为空返回 0。"""
    if not version:
        return 0
    try:
        return int(str(version).split('.')[0])
    except (ValueError, IndexError):
        return 0

# endregion


# region ======== PaddleOCR 2.x 封装 ========

class PaddleOcrV2(OcrEngine):
    """
    基于 paddleocr **2.x** API 的本地 OCR 策略实现。

    依赖 ``paddleocr>=2.7,<3``。注意：3.x 改了 API，本类不可用于 3.x，
    请改用 :class:`PaddleOcrV3` 或自动分发器 :class:`PaddleOcr`。

    特性:
        - 支持中英文等多种语言识别
        - 内置角度分类器，支持倾斜文本识别
        - 基于 PaddlePaddle 深度学习框架

    示例::

        from baibao.ai.ocr.paddle_ocr import PaddleOcrV2
        from pykunlun.ai.ocr import OcrCfg

        ocr = PaddleOcrV2(OcrCfg())                              # 默认（中英文 + 角度分类）
        ocr = PaddleOcrV2(OcrCfg(lang='en', use_angle_cls=False))
    """

    engine_type = 'paddle2'

    def __init__(self, cfg: OcrCfg) -> None:
        """
        初始化 paddleocr 2.x 策略。

        Args:
            cfg: OCR 配置（``lang`` / ``use_angle_cls`` 生效）。

        Raises:
            ImportError: paddleocr 未安装。本类不做自动安装，请先手动安装
                ``paddleocr>=2.7,<3``，或直接使用 :class:`PaddleOcr`（自动安装并按版本分发）。
        """
        super().__init__(cfg)

        try:
            from paddleocr import PaddleOCR
        except ImportError as e:
            raise ImportError(
                "paddleocr 未安装，无法初始化 PaddleOcrV2。\n"
                "请先安装 2.x：pip install \"paddleocr>=2.7,<3\"\n"
                "或直接使用 PaddleOcr（自动按已装版本选择 V2/V3）。"
            ) from e

        self._lang: str = cfg.lang
        self._use_angle_cls: bool = cfg.use_angle_cls
        self._ocr = PaddleOCR(use_angle_cls=cfg.use_angle_cls, lang=cfg.lang)

    @property
    def lang(self) -> str:
        """获取当前配置的语言代码，如 ``'ch'``（中英）、``'en'``（英文）。"""
        return self._lang

    @property
    def use_angle_cls(self) -> bool:
        """获取是否启用角度分类器。"""
        return self._use_angle_cls

    def _recognize_array(self, image: 'np.ndarray[Any, np.dtype[Any]]') -> list[OcrResult]:
        """
        调用 paddleocr 2.x 识别图像数组。

        2.x 返回结构为 ``[[[bbox, [text, confidence]], ...]]``：外层是页面、
        内层是文本行。这里仅做格式映射，清洗与空白过滤由基类统一处理。

        Args:
            image: OpenCV 图像数组（BGR 格式）。

        Returns:
            :class:`OcrResult` 对象列表。
        """
        # 本类仅运行于 paddleocr 2.x（分发器 PaddleOcr 按 major version 分发），
        # 2.x 的 .ocr() 是正当 API；本机装 3.x 时 pyright 才把它标为 deprecated
        # （3.x 用 .predict()），连带 Unknown 均为跨版本类型标注错位，故行级豁免。
        result: Any = self._ocr.ocr(image, cls=self._use_angle_cls)  # pyright: ignore[reportDeprecated, reportUnknownMemberType, reportUnknownVariableType]
        if not result or not result[0]:
            return []

        return [
            OcrResult(
                text=line[1][0],
                bbox=[(int(p[0]), int(p[1])) for p in line[0]],
                confidence=float(line[1][1]),
            )
            for line in result[0]
        ]

# endregion


# region ======== PaddleOCR 3.x 封装 ========

class PaddleOcrV3(OcrEngine):
    """
    基于 paddleocr **3.x** API 的本地 OCR 策略实现。

    依赖 ``paddleocr>=3``。3.x 相对 2.x 的关键变化：
    构造参数 ``use_angle_cls`` 更名为 ``use_textline_orientation``；
    识别方法由 ``.ocr()`` 改为 ``.predict()``；返回结构改为 ``Result`` 对象列表，
    文字 / 置信度 / 框分别落在 ``res.json`` 的 ``rec_texts`` / ``rec_scores`` / ``rec_polys``。

    为对齐 2.x 的识别范围（仅做 文本检测+识别+行方向分类），默认关闭 3.x 新增的
    文档方向分类（``use_doc_orientation_classify``）与文本图像矫正（``use_doc_unwarping``），
    避免额外加载两个模型、拖慢首屏。

    示例::

        from baibao.ai.ocr.paddle_ocr import PaddleOcrV3
        from pykunlun.ai.ocr import OcrCfg

        ocr = PaddleOcrV3(OcrCfg())                                       # 默认
        ocr = PaddleOcrV3(OcrCfg(lang='en', use_angle_cls=False))
        ocr = PaddleOcrV3(OcrCfg(gpu=True, cpu_threads=8))                # GPU + 多线程
    """

    engine_type = 'paddle3'

    def __init__(self, cfg: OcrCfg) -> None:
        """
        初始化 paddleocr 3.x 策略。

        Args:
            cfg: OCR 配置。``use_angle_cls`` 映射为 3.x 的 ``use_textline_orientation``；
                ``gpu`` 映射为 ``device='gpu:0'``（需 ``paddlepaddle-gpu``）；
                ``cpu_threads`` 控制 CPU 推理线程数（``None`` 走默认）。

        Raises:
            ImportError: paddleocr 未安装。本类不做自动安装，请先手动安装
                ``paddleocr>=3``，或直接使用 :class:`PaddleOcr`。
        """
        super().__init__(cfg)

        try:
            from paddleocr import PaddleOCR
        except ImportError as e:
            raise ImportError(
                "paddleocr 未安装，无法初始化 PaddleOcrV3。\n"
                "请先安装 3.x：pip install \"paddleocr>=3\"\n"
                "或直接使用 PaddleOcr（自动按已装版本选择 V2/V3）。"
            ) from e

        self._lang: str = cfg.lang
        self._use_textline_orientation: bool = cfg.use_angle_cls
        # PaddleOCR 3.x 通用推理参数：device / cpu_threads / enable_mkldnn。
        # enable_mkldnn=False：规避 paddlepaddle 3.3.x 在 CPU + mkldnn 下的 PIR bug
        # （改走 run_mode='paddle' 纯 CPU）；GPU 场景 mkldnn 本就不适用，无副作用。
        # 注意：CPU 环境下底层（paddlex / torch）会打印若干良性 UserWarning（典型如
        # "'pin_memory' ... no accelerator is found"、"No ccache found"），属第三方库
        # 内部行为，不影响识别结果，此处不拦截。详见 docs/ai.md「PaddleOCR 版本与已知坑」。
        kwargs: dict[str, Any] = {
            'use_doc_orientation_classify': False,
            'use_doc_unwarping': False,
            'use_textline_orientation': cfg.use_angle_cls,
            'lang': cfg.lang,
            'enable_mkldnn': False,
        }
        if cfg.gpu:
            kwargs['device'] = 'gpu:0'
        if cfg.cpu_threads is not None:
            kwargs['cpu_threads'] = cfg.cpu_threads
        # paddleocr 无类型标注（项目不做 stubs）：实例收口为显式 Any——
        # reportUnknown* 只报 Unknown、不报显式 Any；返回结构在用到处按真实形状注解。
        self._ocr: Any = PaddleOCR(**kwargs)

    @property
    def lang(self) -> str:
        """获取当前配置的语言代码。"""
        return self._lang

    @property
    def use_textline_orientation(self) -> bool:
        """获取是否启用文本行方向分类。"""
        return self._use_textline_orientation

    def _recognize_array(self, image: 'np.ndarray[Any, np.dtype[Any]]') -> list[OcrResult]:
        """
        调用 paddleocr 3.x 识别图像数组。

        3.x 的 ``.predict()`` 返回 ``Result`` 对象列表（每个输入一张），
        单张图片取 ``result[0]``，其 ``.json`` 是一个 dict：

        - ``rec_texts``：识别出的文本行列表（已按阈值过滤，可能含空串，由基类统一清洗）；
        - ``rec_scores``：每行识别置信度；
        - ``rec_polys``：每行四点框，与 ``rec_texts`` 一一对应。

        Args:
            image: OpenCV 图像数组（BGR 格式）。

        Returns:
            :class:`OcrResult` 对象列表。
        """
        output = self._ocr.predict(image)
        if not output:
            return []

        data: dict[str, Any] = output[0].json
        # paddleocr 3.7+ 把识别结果再包了一层 'res'（结构为 {'res': {...}}），剥掉取内层。
        # 兼容未包裹的旧结构：仅当确实存在 'res' 字典时才剥。
        if isinstance(data, dict) and isinstance(data.get('res'), dict):
            data = data['res']
        texts: list[str] = data.get('rec_texts') or []
        scores: list[float] = data.get('rec_scores') or []
        polys: list[list[Any]] = data.get('rec_polys') or []

        results: list[OcrResult] = []
        for i, text in enumerate(texts):
            confidence = float(scores[i]) if i < len(scores) else 0.0
            bbox: list[tuple[int, int]] = []
            if i < len(polys):
                bbox = [(int(point[0]), int(point[1])) for point in polys[i]]
            results.append(OcrResult(text=text or '', bbox=bbox, confidence=confidence))
        return results

# endregion


# region ======== 自动分发器 ========

class PaddleOcr(OcrEngine):
    """
    PaddleOCR 自动分发器（推荐入口）。

    按本地已安装的 paddleocr 主版本号，自动委托给对应实现：

    - 主版本 ``>= 3`` → :class:`PaddleOcrV3`
    - 主版本 ``2.x`` → :class:`PaddleOcrV2`

    paddleocr 未安装时，自动安装最新版（当前为 3.x，故会落到 V3），
    从而保留"实例化即用"的体验。调用方对外接口与 V2 / V3 完全一致，
    无需关心底层版本差异。

    示例::

        from baibao.ai.ocr import PaddleOcr
        from pykunlun.ai.ocr import OcrCfg

        ocr = PaddleOcr(OcrCfg())                         # 自动选 V2 或 V3
        text = ocr.recognize("image.png")
        print(ocr.impl)                                   # 'paddle3' 或 'paddle2'
        ocr = PaddleOcr(OcrCfg(gpu=True, cpu_threads=8, lang='en'))   # GPU + 多线程 + 英文
    """

    engine_type = 'paddle'

    def __init__(self, cfg: OcrCfg) -> None:
        """
        初始化自动分发器。

        Args:
            cfg: OCR 配置。``use_angle_cls`` 的语义对 V2 / V3 均有效
                （V3 中映射为 ``use_textline_orientation``）；``gpu`` / ``cpu_threads``
                仅 V3 生效（2.x 不支持，忽略）。

        Raises:
            ImportError: paddleocr 未安装且自动安装失败。
        """
        super().__init__(cfg)

        version = get_paddleocr_version()
        if version is None:
            success, msg = pip.install('paddleocr')
            if not success:
                raise ImportError(
                    f"paddleocr 未安装，自动安装失败: {msg}\n"
                    "请手动运行: pip install paddleocr"
                )
            version = get_paddleocr_version()

        if version is None:
            # 已安装却导入失败：通常是依赖（paddlepaddle/paddlex/modelscope/torch）
            # 安装异常或与当前 Python 版本不兼容，重装 paddleocr 本身未必能解决。
            raise ImportError(
                "paddleocr 已安装但无法导入，多半是其依赖（paddlepaddle / paddlex / "
                "modelscope / torch）安装异常或与当前 Python 版本不兼容。\n"
                "可尝试：pip install --force-reinstall paddleocr\n"
                "并确认 torch 能单独导入：python -c \"import torch; print(torch.__version__)\""
            )

        if _major_version(version) >= 3:
            # 委托给 V3：用 replace 造一份 engine_type='paddle3' 的 cfg，
            # 避免与本类已绑定的 engine_type='paddle' 冲突（基类 __init__ 会校验一致性）。
            delegate_cfg = dataclasses.replace(cfg, engine_type='paddle3')
            self._delegate: PaddleOcrV2 | PaddleOcrV3 = PaddleOcrV3(delegate_cfg)
            self._impl = 'paddle3'
        else:
            # 2.x 不直接支持 gpu/cpu_threads，忽略这两个参数（仅 lang/use_angle_cls 生效）。
            delegate_cfg = dataclasses.replace(cfg, engine_type='paddle2')
            self._delegate = PaddleOcrV2(delegate_cfg)
            self._impl = 'paddle2'

    @property
    def impl(self) -> str:
        """实际委托的实现标识：``'paddle3'`` 或 ``'paddle2'``。"""
        return self._impl

    @property
    def lang(self) -> str:
        """获取底层实现配置的语言代码。"""
        return self._delegate.lang

    def _recognize_array(self, image: 'np.ndarray[Any, np.dtype[Any]]') -> list[OcrResult]:
        """转发给底层 V2 / V3 实现（同族类白盒协作）。"""
        return self._delegate._recognize_array(image)  # pyright: ignore[reportPrivateUsage]

# endregion

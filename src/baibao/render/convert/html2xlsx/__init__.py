"""
HTML 转带样式 xlsx 的通用引擎。

把语义 HTML（带 class 与行内样式，无 ``<style>`` 块）转写为保留表格结构的 Excel 工作表：
合并单元格（colspan/rowspan）、边框、字体、对齐、背景填充、列宽、行高、浮动图片与
A4 打印设置。面向"合同/单据 HTML 存档转 Excel"类场景，也可用于任意"表格 + 段落"
型 HTML 的降级转写。

设计要点：
    - 引擎不含任何业务词汇。业务差异（class 到样式的映射、flex 布局摆位、印章锚定等）
      通过 :class:`Html2XlsxProfile` 注入；
    - 不解析 ``<style>`` 块、不做 CSS 级联计算。样式来自三层合成：
      标签默认 → profile 的 class 映射 → 行内 style 属性；
    - 依赖懒加载：bs4/lxml/openpyxl 未安装时经
      :func:`pykunlun.util.modutil.import_module` 自动安装，import 本模块零副作用；
    - 引擎不联网：``<img>`` 的字节数据由调用方下载后经 ``images`` 传入；
    - 降级必须可见：无法解析的样式值、白名单之外的 CSS 属性一律记入
      :class:`Html2XlsxResult` 的 ``warnings``（按首次出现去重），不做静默丢弃。

CSS 到 Excel 的映射口径：
    - ``border: 1px solid #000`` → 对应边 ``Side('thin')``；``border-x: none`` → 该边无边框；
    - ``font-size`` 的 px 值 → pt（× 0.75，96dpi 约定）；``font-weight:700/bold`` → 粗体；
    - ``text-align``/``vertical-align`` → 对齐；``background(-color)`` → 纯色填充；
    - ``width: N%``（表格首行单元格）→ 列宽字符数按百分比分摊总宽预算；
    - ``white-space: pre-line`` 与多行文本 → ``wrap_text=True`` + 单元格内换行；
    - ``colspan``/``rowspan`` → ``merge_cells``，边框应用到合并区每个成员格；
    - 无 Excel 对应物的属性（letter-spacing、mix-blend-mode 等）主动忽略，记入 warnings。

包含子模块：

  - model: 转换契约（业务注入点 :class:`Html2XlsxProfile` 与结果摘要 :class:`Html2XlsxResult`）
  - style_parser: 样式解析（标签语义默认、class 选择器匹配、行内 style 白名单）
  - renderer: 渲染器与对外入口 :func:`convert_html_to_xlsx`
"""

from baibao.office.excel import BorderStyle, CellStyle, ImageAnchor

from .model import Html2XlsxProfile, Html2XlsxResult
from .renderer import convert_html_to_xlsx

__all__ = [
    "BorderStyle",
    "CellStyle",
    "Html2XlsxProfile",
    "Html2XlsxResult",
    "ImageAnchor",
    "convert_html_to_xlsx",
]

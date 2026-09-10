"""
文档转换器包：从源表示产出成稿文档。

render 包内三个子模块按"对 HTML 做什么"分工：html 造 HTML（数据 → 片段），
template 填 HTML（模板 → 实例），convert 消费 HTML（源文档 → 二进制成稿）。
转换器的本质仍是渲染——把源表示的视觉模型转写为目标载体的视觉模型（如
html2xlsx 即 CSS 视觉模型 → Excel 视觉模型），只是投放媒体不同。

包含子模块：

  - html2xlsx: 语义 HTML 转带样式 xlsx 的通用引擎（合同/单据存档转 Excel 等）
"""

from .html2xlsx import (
    BorderStyle,
    CellStyle,
    Html2XlsxProfile,
    Html2XlsxResult,
    ImageAnchor,
    convert_html_to_xlsx,
)

__all__ = [
    'BorderStyle',
    'CellStyle',
    'Html2XlsxProfile',
    'Html2XlsxResult',
    'ImageAnchor',
    'convert_html_to_xlsx',
]

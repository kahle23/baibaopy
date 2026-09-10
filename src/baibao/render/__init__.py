"""
内容渲染包，提供 HTML 片段构建、模板引擎与 HTML 转 xlsx 能力。

包含三个子模块：

  - html: 面向报告场景的 HTML 片段构建（表格、柱状图、折线图、指标卡片）
  - template: 模板引擎（支持 Jinja2 等多种实现）
  - html2xlsx: HTML 转带样式 xlsx 的通用引擎（合同/单据存档转 Excel 等）
"""

from . import html, html2xlsx, template
from .html2xlsx import (
    BorderStyle,
    CellStyle,
    Html2XlsxProfile,
    Html2XlsxResult,
    ImageAnchor,
    convert_html_to_xlsx,
)
from .template import Jinja2Engine, TemplateEngine

__all__ = [
    'BorderStyle',
    'CellStyle',
    'Html2XlsxProfile',
    'Html2XlsxResult',
    'ImageAnchor',
    'Jinja2Engine',
    'TemplateEngine',
    'convert_html_to_xlsx',
    'html',
    'html2xlsx',
    'template',
]

"""
内容渲染包，提供 HTML 片段构建、模板引擎与 HTML 转 xlsx 能力。

按"对 HTML 做什么"划分三个子模块：

  - html: 面向报告场景的 HTML 片段构建（表格、柱状图、折线图、指标卡片）
  - template: 模板引擎（支持 Jinja2 等多种实现）
  - convert: 文档转换器，从源表示产出成稿文档（html2xlsx: HTML 转带样式 xlsx 等）
"""

from . import convert, html, template
from .convert.html2xlsx import (
    Html2XlsxProfile,
    Html2XlsxResult,
    convert_html_to_xlsx,
)
from .template import Jinja2Engine, TemplateEngine

__all__ = [
    'Html2XlsxProfile',
    'Html2XlsxResult',
    'Jinja2Engine',
    'TemplateEngine',
    'convert',
    'convert_html_to_xlsx',
    'html',
    'template',
]

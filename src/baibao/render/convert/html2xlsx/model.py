"""
html2xlsx 转换契约：业务注入点与结果摘要。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from baibao.office.excel import CellStyle, ImageAnchor

# 默认总宽预算（字符数）：A4 纵向、10pt 左右字号下的可用列宽合计
_DEFAULT_TOTAL_WIDTH_CHARS = 118.0
# 基础网格列数：sheet 物理列等宽，各表按百分比映射到基础列段（合并组合），
# 使同 sheet 的多张表格互不干扰地保持各自的列宽比例
_DEFAULT_BASE_GRID_COLS = 24


@dataclass
class Html2XlsxProfile:
    """
    转换规则集：业务方通过它把"class 语义"注入通用引擎。

    Attributes:
        container_class: 内容根节点的 class；None 时取 ``<body>``（或整棵树）。
        class_styles: 样式映射。键支持 ``".cls"``、``"tag"`` 与两级作用域 ``".scope .cls"``；
            值为该选择器命中的样式覆盖（命中多态时按特异性从低到高依次 merge）。
        full_width_border: 表格默认全线框。True 时表格单元格四边补细线，
            行内样式的显式 ``border: none`` 仍可关闭单边。
        column_layouts: 多栏容器规则。键为容器 class，值为各子块相对宽度元组
            （如 ``{"contract-head": (1.0, 1.0)}``）；未登记的容器按纵向堆叠降级。
        table_style_rule: 位置规则回调 ``(table_class, row_idx, col_idx, style, total_rows) -> style``，
            用于 ``tr:nth-child`` 类的按位覆盖（如签署栏"第 2 行第 1 列去底线"）。
        image_rule: 图片规则回调 ``(img_node, containing) -> ImageAnchor | None``。
            ``containing`` 为 ``("table", table_class, row, col)`` 或 ``("block", class)``；
            返回 None 则跳过该图。
        total_width_chars: 列宽总预算（字符数）。
        base_grid_cols: 基础网格列数。sheet 物理列等宽，每张表按自身百分比映射到
            基础列段（合并组合），多表共存时互不干扰地保持各自列宽比例。
        row_height_factor: 行高系数（行高 ≈ 行数 × 字号pt × 该系数 + 固定余量）。
        landscape: 打印方向；False=A4 纵向。
    """

    container_class: str | None = None
    class_styles: dict[str, CellStyle] = field(default_factory=dict)
    full_width_border: bool = True
    column_layouts: dict[str, tuple[float, ...]] = field(default_factory=dict)
    table_style_rule: Callable[[str | None, int, int, CellStyle, int], CellStyle] | None = None
    image_rule: Callable[[Any, tuple], ImageAnchor | None] | None = None
    total_width_chars: float = _DEFAULT_TOTAL_WIDTH_CHARS
    base_grid_cols: int = _DEFAULT_BASE_GRID_COLS
    row_height_factor: float = 1.35
    landscape: bool = False
    table_column_widths: dict[str, tuple[float, ...]] = field(default_factory=dict)


@dataclass
class Html2XlsxResult:
    """
    转换结果摘要。
    """

    output_path: Path
    rows: int
    cols: int
    merges: int
    images: int
    warnings: list[str] = field(default_factory=list)

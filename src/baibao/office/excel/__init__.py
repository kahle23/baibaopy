"""
Excel 电子表格子域。

提供 Excel 格式家族（xls/xlsx/xlsm）通用的电子表格模型与操作能力。
模型层为纯 dataclass，字段是电子表格的通用样式词汇，不绑定具体实现库，
由各实现层负责到目标库（openpyxl/xlwt 等）的映射。

包含子模块：

  - model: 单元格样式与浮动图片锚定的通用模型
"""

from .model import (
    BorderStyle,
    CellStyle,
    ImageAnchor,
)

__all__ = [
    "BorderStyle",
    "CellStyle",
    "ImageAnchor",
]

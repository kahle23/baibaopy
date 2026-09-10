"""
Excel 电子表格通用模型。

与实现库解耦的纯数据模型：描述电子表格的单元格样式与浮动图片锚定等通用概念，
供 Excel 家族各格式（xls/xlsx/xlsm）的读写实现共用。
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass
class BorderStyle:
    """
    单边框样式，``style=None`` 表示显式无边框。
    """

    style: str | None = "thin"  # 线型（如 thin/medium/dashed/double），None 表示显式无边框
    color: str | None = "FF000000"  # 线色，ARGB 八位十六进制（如 FF000000 为黑）


@dataclass
class CellStyle:
    """
    单元格样式模型。字段为 ``None`` 表示"未指定"，合成时被更高优先级覆盖。
    """

    bold: bool | None = None  # 是否加粗
    italic: bool | None = None  # 是否斜体
    underline: bool | None = None  # 是否下划线
    font_name: str | None = None  # 字体名（如 宋体/Arial）
    font_size_pt: float | None = None  # 字号，单位 pt
    font_color: str | None = None  # 字体颜色，ARGB 八位十六进制
    fill_color: str | None = None  # 背景填充色（纯色），ARGB 八位十六进制
    halign: str | None = None  # 水平对齐：left/center/right
    valign: str | None = None  # 垂直对齐：top/center/bottom
    wrap_text: bool | None = None  # 是否自动换行
    border_top: BorderStyle | None = None  # 上边框
    border_bottom: BorderStyle | None = None  # 下边框
    border_left: BorderStyle | None = None  # 左边框
    border_right: BorderStyle | None = None  # 右边框
    width_percent: float | None = None  # 相对宽度意图（列宽百分比，如 50.0），供表格列计划使用

    def merge(self, override: CellStyle) -> CellStyle:
        """
        用 ``override`` 中的非 None 字段覆盖当前值，返回新对象（不可变合成）。
        """
        merged = CellStyle(**{k: v for k, v in self.__dict__.items()})
        for name, value in override.__dict__.items():
            if value is not None:
                setattr(merged, name, value)
        return merged

    def with_grid_borders(self, side: BorderStyle) -> CellStyle:
        """
        四边补上指定边框（已有显式边框的边不动）。
        """
        return replace(
            self,
            border_top=self.border_top or side,
            border_bottom=self.border_bottom or side,
            border_left=self.border_left or side,
            border_right=self.border_right or side,
        )


@dataclass
class ImageAnchor:
    """
    浮动图片锚定描述。
    """

    row: int  # 锚定行号（1 起）；0 表示由实现层落位到当前单元格所在行
    col: int  # 锚定列号（1 起）；0 表示由实现层落位到当前单元格所在列
    width_px: int  # 显示宽度，px
    height_px: int  # 显示高度，px
    offset_x_px: int = 0  # 相对锚点的水平偏移，px
    offset_y_px: int = 0  # 相对锚点的垂直偏移，px

"""
html2xlsx 渲染器：基础网格布局、块级/表格渲染与图片锚定，附对外入口。
"""

from __future__ import annotations

import io
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from unicodedata import east_asian_width

from pykunlun.util import modutil

from baibao.office.excel import BorderStyle, CellStyle, ImageAnchor

from .model import Html2XlsxProfile, Html2XlsxResult
from .style_parser import _classes_of, _first_class, _resolve_style

# region ======== 常量与文本工具 ========
# px → EMU（openpyxl 图片锚定单位）
_PX_TO_EMU = 9525
# Excel 列宽单位基于工作簿默认字体（Calibri 11）；单元格字号更小时每单位能容纳更多字符
_BASE_FONT_PT = 11.0

# 行内标签：文本连续拼接不换行；其余（p/div/li/table…）作为块级换行
_INLINE_TAGS = {"span", "b", "strong", "i", "em", "u", "a", "label", "small", "sub", "sup", "font"}


def _block_text(node: Any) -> str:
    """
    块级感知的文本提取：行内内容连续拼接，块级内容之间换行，``<br>`` 转换行。
    """
    if node.name is None:
        return str(node)
    if node.name == "br":
        return "\n"
    inner = "".join(_block_text(child) for child in node.children)
    if node.name in _INLINE_TAGS:
        return inner
    if inner and not inner.startswith("\n"):
        return "\n" + inner
    return inner


def _display_width(text: str) -> int:
    """
    按终端显示宽度计字符宽：东亚宽字符（全角/CJK）记 2，其余记 1。
    """
    return sum(2 if east_asian_width(ch) in ("W", "F") else 1 for ch in text)
# endregion


# region ======== 渲染器 ========
class _HtmlToXlsxRenderer:
    """
    单次转换的渲染上下文（openpyxl 依赖在构造时经 modutil 懒加载）。
    """

    def __init__(self, profile: Html2XlsxProfile, images: dict[str, bytes]) -> None:
        self.profile = profile
        self.image_bytes = images
        self.warnings: list[str] = []
        self.pending_images: list[tuple[bytes, ImageAnchor]] = []
        # 基础网格：sheet 物理列等宽，各表按百分比映射到基础列段
        self.base_cols = max(8, profile.base_grid_cols)
        self.openpyxl = modutil.import_module("openpyxl")
        self.utils = modutil.import_module("openpyxl.utils")
        self.drawing_image = modutil.import_module("openpyxl.drawing.image")
        self.drawing_anchor = modutil.import_module("openpyxl.drawing.spreadsheet_drawing")
        self.drawing_xdr = modutil.import_module("openpyxl.drawing.xdr")
        self.worksheet_props = modutil.import_module("openpyxl.worksheet.properties")
        self.bs4 = modutil.import_module("bs4", "beautifulsoup4")
        self.workbook: Any = None
        self.sheet: Any = None
        self.row = 1
        self.last_content_row = 0

    # region ======== 主流程 ========
    def convert(self, html: str, output_path: Path, sheet_name: str | None) -> Html2XlsxResult:
        soup = self.bs4.BeautifulSoup(html, "html.parser")
        root = soup.find(class_=self.profile.container_class) if self.profile.container_class else None
        if self.profile.container_class and root is None:
            self.warnings.append(f"未找到容器 class={self.profile.container_class}，退化为 body")
            root = soup.body or soup
        elif root is None:
            root = soup.body or soup
        self.workbook = self.openpyxl.Workbook()
        self.sheet = self.workbook.active
        self.sheet.title = sheet_name or "Sheet1"
        self._init_column_dimensions()
        for child in [c for c in root.children if getattr(c, "name", None)]:
            self._render_block(child, root)
        self._flush_images()
        self._apply_page_setup()
        self.workbook.save(str(output_path))
        # 同类降级只保留首条，warnings 账单保持可读
        self.warnings = list(dict.fromkeys(self.warnings))
        return Html2XlsxResult(
            output_path=output_path,
            rows=self.last_content_row,
            cols=self.base_cols,
            merges=len(self.sheet.merged_cells.ranges),
            images=len(self.pending_images),
            warnings=self.warnings,
        )
    # endregion

    # region ======== 块级渲染 ========
    def _render_block(self, block: Any, root: Any, base_style: CellStyle | None = None) -> None:
        if block.name in ("script", "style", "br", "hr"):
            return
        if block.name == "table":
            self._render_table(block, root)
            return
        layout = self._column_layout_of(block)
        if layout is not None:
            self._render_columns(block, layout, root)
            return
        if block.find("table"):
            for child in [c for c in block.children if getattr(c, "name", None)]:
                self._render_block(child, root, base_style)
            return
        text = _block_text(block).strip()
        if not text:
            return
        # 容器块（自身无直接文本、子块各自带文本，如 .clauses 下的多条 .clause-item）
        # 逐子块成行，容器样式作为基础样式向下继承；混合内容块（直接文本 + 行内子元素，
        # 如 "标签：<span class='blank'>值</span>"）保持整行渲染，避免拆散标签与值。
        element_children = [c for c in block.children if getattr(c, "name", None)]
        direct_text = "".join(
            str(c) for c in block.children if not getattr(c, "name", None)
        ).strip()
        if (
            element_children
            and not direct_text
            and all(c.get_text(strip=True) for c in element_children)
        ):
            inherited = (base_style or CellStyle()).merge(
                _resolve_style(block, root, self.profile, self.warnings))
            for child in element_children:
                self._render_block(child, root, inherited)
            return
        style = (base_style or CellStyle()).merge(
            _resolve_style(block, root, self.profile, self.warnings))
        style = style.merge(CellStyle(wrap_text=True, valign="top"))
        if style.font_size_pt is None:
            style = replace(style, font_size_pt=9.0)
        self._write_text_row(text, style, img_host=block)

    def _column_layout_of(self, block: Any) -> tuple[float, ...] | None:
        for cls in _classes_of(block):
            if cls in self.profile.column_layouts:
                return self.profile.column_layouts[cls]
        return None

    def _render_columns(self, container: Any, ratios: tuple[float, ...], root: Any) -> None:
        """
        多栏容器：各子块按相对宽度映射到并排的基础列段（Excel 无 flex 的近似摆法）。
        """
        children = [c for c in container.children if getattr(c, "name", None) and c.get_text(strip=True)]
        if not children:
            return
        if len(ratios) < len(children):
            ratios = ratios + (1.0,) * (len(children) - len(ratios))
        total = sum(ratios[: len(children)]) or 1.0
        col = 1
        start_row = self.row
        end_rows: list[int] = []
        for child, ratio in zip(children, ratios):
            span = max(2, round(self.base_cols * ratio / total))
            span = min(span, self.base_cols - col + 1)
            marker_row = self.row
            text = _block_text(child).strip()
            style = _resolve_style(child, root, self.profile, self.warnings).merge(
                CellStyle(wrap_text=True, valign="top")
            )
            if style.font_size_pt is None:
                style = replace(style, font_size_pt=9.0)
            self._write_text_row(text, style, col_start=col, col_span=span, img_host=child)
            end_rows.append(self.row)
            self.row = marker_row
            col += span
        self.row = max(end_rows) if end_rows else start_row + 1

    def _write_text_row(
        self,
        text: str,
        style: CellStyle,
        col_start: int = 1,
        col_span: int | None = None,
        img_host: Any | None = None,
    ) -> None:
        span = min(col_span or self.base_cols, self.base_cols - col_start + 1)
        r = self.row
        cell = self.sheet.cell(row=r, column=col_start, value=text)
        self._apply_style(cell, style)
        if span > 1:
            self.sheet.merge_cells(
                start_row=r, start_column=col_start, end_row=r, end_column=col_start + span - 1
            )
            for c in range(col_start, col_start + span):
                self._apply_style(self.sheet.cell(row=r, column=c), style)
        if img_host is not None:
            self._anchor_images_of(img_host, ("block", _first_class(img_host)), row=r, col=col_start)
        # 行高：按换行数与显示宽度估算（列宽单位按字号缩放：小字每单位放更多字符）
        font_pt = style.font_size_pt or 9.0
        width_chars = self.profile.total_width_chars * span / self.base_cols
        seg_lines = sum(self._lines_for_width(seg, width_chars, font_pt) for seg in text.split("\n"))
        self.sheet.row_dimensions[r].height = max(14.0, seg_lines * font_pt * self.profile.row_height_factor + 4)
        self.last_content_row = max(self.last_content_row, r)
        self.row += 1

    def _lines_for_width(self, text: str, width_chars: float, font_pt: float) -> int:
        """
        估算文本在指定列宽（字符单位）下的行数，考虑字号相对默认字体的缩放。
        """
        usable = max(4.0, width_chars * 0.95 * (_BASE_FONT_PT / font_pt))
        return max(1, -(-_display_width(text) // int(usable)))

    def _init_column_dimensions(self) -> None:
        """
        基础网格等宽：每根物理列宽 = 总预算 / 基础列数。
        """
        width = round(max(4.0, self.profile.total_width_chars / self.base_cols), 2)
        for c in range(1, self.base_cols + 1):
            self.sheet.column_dimensions[self.utils.get_column_letter(c)].width = width
    # endregion

    # region ======== 表格渲染 ========
    def _render_table(self, table: Any, root: Any) -> None:
        table_cls = _first_class(table)
        rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        if not rows:
            return
        # 占位网格展开：处理 colspan/rowspan，求网格尺寸
        occupied: dict[tuple[int, int], str] = {}
        cells: list[dict[str, Any]] = []
        max_cols = 0
        for r_idx, tr in enumerate(rows):
            c_cursor = 0
            for cell_node in tr.find_all(["td", "th"], recursive=False):
                while (r_idx, c_cursor) in occupied:
                    c_cursor += 1
                cs = int(cell_node.get("colspan", 1) or 1)
                rs = int(cell_node.get("rowspan", 1) or 1)
                style = _resolve_style(cell_node, root, self.profile, self.warnings)
                if self.profile.full_width_border:
                    style = style.with_grid_borders(BorderStyle())
                if self.profile.table_style_rule:
                    style = self.profile.table_style_rule(table_cls, r_idx, c_cursor, style, len(rows))
                if style.font_size_pt is None:
                    style = replace(style, font_size_pt=9.0)
                if style.valign is None:
                    style = replace(style, valign="center")
                has_block = cell_node.find(["p", "div", "li", "table"])
                text = cell_node.get_text("\n", strip=True) if has_block else _block_text(cell_node).strip()
                cells.append({"r": r_idx, "c": c_cursor, "cs": cs, "rs": rs, "style": style,
                              "text": text, "node": cell_node})
                for rr in range(rs):
                    for cc in range(cs):
                        occupied[(r_idx + rr, c_cursor + cc)] = "x"
                c_cursor += cs
                max_cols = max(max_cols, c_cursor)
        total_rows = (max((r for r, _ in occupied), default=-1) + 1) if occupied else 0
        # 列计划：本表各逻辑列映射到基础列段 [b[i], b[i+1])，比例互不影响其他表
        boundaries = self._table_boundaries(table_cls, rows[0], max_cols)
        # 写单元格：逻辑列映射为基础列段，值写在段首，样式铺满合并区每个成员格
        for cell in cells:
            r0, c0, cs, rs, style = cell["r"], cell["c"], cell["cs"], cell["rs"], cell["style"]
            base_row = self.row + r0
            col_start = boundaries[c0] + 1
            col_end = boundaries[min(c0 + cs, max_cols)]
            value = cell["text"] or None
            top_left = self.sheet.cell(row=base_row, column=col_start, value=value)
            self._apply_style(top_left, style)
            for rr in range(rs):
                for cc in range(col_start, col_end):
                    self._apply_style(self.sheet.cell(row=base_row + rr, column=cc), style)
            self.last_content_row = max(self.last_content_row, base_row + rs - 1)
            if col_end > col_start or rs > 1:
                self.sheet.merge_cells(
                    start_row=base_row, start_column=col_start,
                    end_row=base_row + rs - 1, end_column=col_end,
                )
            td_node = cell["node"]
            if td_node.find("img"):
                self._anchor_images_of(td_node, ("table", table_cls, r0, c0),
                                       row=base_row, col=col_start)
        # 行高：按本表各逻辑列在基础网格中的实际字符宽度估算
        for r_idx in range(total_rows):
            sheet_r = self.row + r_idx
            lines = 1
            font_pt = 9.0
            for cell in cells:
                if cell["r"] <= r_idx < cell["r"] + cell["rs"]:
                    segs = str(cell["text"]).split("\n") if cell["text"] else [""]
                    c0, cs = cell["c"], cell["cs"]
                    span_chars = self.profile.total_width_chars * (
                        boundaries[min(c0 + cs, max_cols)] - boundaries[c0]) / self.base_cols
                    cell_lines = sum(
                        self._lines_for_width(seg, span_chars, cell["style"].font_size_pt or 9.0)
                        for seg in segs
                    )
                    lines = max(lines, cell_lines)
                    font_pt = max(font_pt, cell["style"].font_size_pt or 9.0)
            self.sheet.row_dimensions[sheet_r].height = max(
                14.0, lines * font_pt * self.profile.row_height_factor + 3)
        self.row += total_rows + 1  # 表格后空一行

    def _table_boundaries(self, table_cls: str | None, first_row: Any, max_cols: int) -> list[int]:
        """
        构建本表的基础列段边界：profile 指定百分比优先，其次首行 width%，缺省均分。

        Returns:
            长度 max_cols+1 的边界数组（0 起），b[i]~b[i+1] 为第 i 逻辑列占用的基础列段，
            单调递增且 b[0]=0、b[max_cols]=base_cols。
        """
        percents: list[float] | None = None
        profile_widths = self.profile.table_column_widths.get(table_cls) if table_cls else None
        if profile_widths:
            percents = [float(p) for p in profile_widths[:max_cols]]
            if len(percents) < max_cols:
                percents += [0.0] * (max_cols - len(percents))
        else:
            inline: dict[int, float] = {}
            c_cursor = 0
            for cell_node in first_row.find_all(["td", "th"], recursive=False):
                while c_cursor in inline:
                    c_cursor += 1
                match = re.search(r"width\s*:\s*([\d.]+)\s*%", cell_node.get("style", ""))
                if match:
                    inline[c_cursor] = float(match.group(1))
                c_cursor += int(cell_node.get("colspan", 1) or 1)
            if inline:
                percents = [inline.get(i, 0.0) for i in range(max_cols)]
        if percents is None or sum(percents) <= 0:
            percents = [100.0 / max_cols] * max_cols
        else:
            # 未标注百分比的列均摊剩余
            declared = sum(percents)
            if declared < 100:
                zero_count = sum(1 for p in percents if p <= 0)
                if zero_count:
                    share = (100 - declared) / zero_count
                    percents = [p if p > 0 else share for p in percents]
        boundaries = [0]
        cumulative = 0.0
        for pct in percents:
            cumulative += pct
            boundaries.append(min(self.base_cols, max(1, round(self.base_cols * cumulative / 100))))
        boundaries[-1] = self.base_cols
        for i in range(1, len(boundaries)):
            boundaries[i] = max(boundaries[i], boundaries[i - 1] + 1)
        return boundaries
    # endregion

    # region ======== 图片锚定 ========
    def _anchor_images_of(self, host: Any, containing: tuple, row: int = 0, col: int = 1) -> None:
        if self.profile.image_rule is None:
            return
        for img in host.find_all("img"):
            src = img.get("src") or ""
            data = self.image_bytes.get(src)
            if data is None:
                if src:
                    self.warnings.append(f"图片无字节数据，跳过：{src[:80]}")
                continue
            anchor = self.profile.image_rule(img, containing)
            if anchor is None:
                continue
            if anchor.row == 0:
                anchor = replace(anchor, row=row, col=col)
            self.pending_images.append((data, anchor))

    def _flush_images(self) -> None:
        image_cls = self.drawing_image.Image
        anchor_cls = self.drawing_anchor.OneCellAnchor
        marker_cls = self.drawing_anchor.AnchorMarker
        size_cls = self.drawing_xdr.XDRPositiveSize2D

        for data, anchor in self.pending_images:
            image = image_cls(io.BytesIO(data))
            image.width, image.height = anchor.width_px, anchor.height_px
            from_marker = marker_cls(
                col=anchor.col - 1, colOff=anchor.offset_x_px * _PX_TO_EMU,
                row=anchor.row - 1, rowOff=anchor.offset_y_px * _PX_TO_EMU,
            )
            ext = size_cls(cx=anchor.width_px * _PX_TO_EMU, cy=anchor.height_px * _PX_TO_EMU)
            image.anchor = anchor_cls(_from=from_marker, ext=ext)
            self.sheet.add_image(image)
    # endregion

    # region ======== 样式落格与页面设置 ========
    def _apply_style(self, cell: Any, style: CellStyle) -> None:
        font_kw: dict[str, Any] = {}
        if style.bold is not None:
            font_kw["bold"] = style.bold
        if style.italic is not None:
            font_kw["italic"] = style.italic
        if style.underline:
            font_kw["underline"] = "single"
        if style.font_name:
            font_kw["name"] = style.font_name
        if style.font_size_pt:
            font_kw["size"] = style.font_size_pt
        if style.font_color:
            font_kw["color"] = style.font_color
        if font_kw:
            cell.font = self.openpyxl.styles.Font(**font_kw)
        align_kw: dict[str, Any] = {}
        if style.halign:
            align_kw["horizontal"] = style.halign
        if style.valign:
            align_kw["vertical"] = style.valign
        if style.wrap_text:
            align_kw["wrap_text"] = True
        if align_kw:
            cell.alignment = self.openpyxl.styles.Alignment(**align_kw)
        if style.fill_color:
            cell.fill = self.openpyxl.styles.PatternFill("solid", fgColor=style.fill_color)

        def side(border: BorderStyle | None) -> Any:
            if border is None or border.style is None:
                return self.openpyxl.styles.Side()
            return self.openpyxl.styles.Side(style=border.style, color=border.color)

        cell.border = self.openpyxl.styles.Border(
            top=side(style.border_top), bottom=side(style.border_bottom),
            left=side(style.border_left), right=side(style.border_right),
        )

    def _apply_page_setup(self) -> None:
        sheet = self.sheet
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.orientation = "landscape" if self.profile.landscape else "portrait"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr = self.worksheet_props.PageSetupProperties(fitToPage=True)
        margins = sheet.page_margins
        margins.left = margins.right = 0.39
        margins.top = margins.bottom = 0.49
    # endregion
# endregion


# region ======== 对外入口 ========
def convert_html_to_xlsx(
    html: str,
    output_path: str | Path,
    *,
    profile: Html2XlsxProfile | None = None,
    sheet_name: str | None = None,
    images: dict[str, bytes] | None = None,
) -> Html2XlsxResult:
    """
    把 HTML 转写为带样式的 xlsx 文件。

    Args:
        html: 语义 HTML 字符串（带 class/行内样式，无 ``<style>`` 块）。
        output_path: 输出 xlsx 路径。
        profile: 转换规则集；None 时使用通用默认（全线框表格 + 无多栏规则）。
        sheet_name: 工作表名，默认 Sheet1。
        images: ``<img src>`` 到图片字节数据的映射，引擎不联网下载。

    Returns:
        :class:`Html2XlsxResult`：行列数、合并数、图片数与降级警告清单
        （同类降级按首次出现去重）。

    Raises:
        ImportError: bs4/openpyxl 依赖自动安装失败时抛出。
        OSError: 输出路径不可写时抛出。
    """
    renderer = _HtmlToXlsxRenderer(profile or Html2XlsxProfile(), images or {})
    return renderer.convert(html, Path(output_path), sheet_name)
# endregion

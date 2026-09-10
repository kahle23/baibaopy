"""HTML 转带样式 xlsx 的通用引擎。

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
    - 引擎不联网：``<img>`` 的字节数据由调用方下载后经 ``images`` 传入。

CSS 到 Excel 的映射口径：
    - ``border: 1px solid #000`` → 对应边 ``Side('thin')``；``border-x: none`` → 该边无边框；
    - ``font-size`` 的 px 值 → pt（× 0.75，96dpi 约定）；``font-weight:700/bold`` → 粗体；
    - ``text-align``/``vertical-align`` → 对齐；``background(-color)`` → 纯色填充；
    - ``width: N%``（表格首行单元格）→ 列宽字符数按百分比分摊总宽预算；
    - ``white-space: pre-line`` 与多行文本 → ``wrap_text=True`` + 单元格内换行；
    - ``colspan``/``rowspan`` → ``merge_cells``，边框应用到合并区每个成员格；
    - 无 Excel 对应物的属性（letter-spacing、mix-blend-mode 等）主动忽略，记入 warnings。

Usage:
    from baibao.render.html2xlsx import convert_html_to_xlsx, Html2XlsxProfile

    result = convert_html_to_xlsx(html, "out.xlsx")                  # 通用默认样式
    result = convert_html_to_xlsx(html, "out.xlsx", profile=profile) # 业务 profile
    print(result.warnings)                                           # 降级/忽略清单
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable
from unicodedata import east_asian_width

from pykunlun.util import modutil

__all__ = [
    "BorderStyle",
    "CellStyle",
    "Html2XlsxProfile",
    "Html2XlsxResult",
    "ImageAnchor",
    "convert_html_to_xlsx",
]

# px → pt（CSS 96dpi 约定），以及 px → EMU（openpyxl 图片锚定单位）
_PX_TO_PT = 0.75
_PX_TO_EMU = 9525

# 默认总宽预算（字符数）：A4 纵向、10pt 左右字号下的可用列宽合计
_DEFAULT_TOTAL_WIDTH_CHARS = 118.0
# 单个西文字符折算列宽的像素系数（Calibri 11 的 '0' ≈ 7px），用于行高估算
_CHAR_PX = 7.0

# 行内样式属性的白名单解析（其余属性忽略）
_COLOR_RE = re.compile(r"^(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})|[a-zA-Z]+)$")
_BORDER_RE = re.compile(r"^\s*(\d+)px\s+\w+\s+(#\w+|[a-zA-Z]+)\s*$")
_BORDER_SIDE_PROPS = ("border-top", "border-bottom", "border-left", "border-right")

# 行内标签：文本连续拼接不换行；其余（p/div/li/table…）作为块级换行
_INLINE_TAGS = {"span", "b", "strong", "i", "em", "u", "a", "label", "small", "sub", "sup", "font"}


def _block_text(node: Any) -> str:
    """块级感知的文本提取：行内内容连续拼接，块级内容之间换行，``<br>`` 转换行。"""
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
    """按终端显示宽度计字符宽：东亚宽字符（全角/CJK）记 2，其余记 1。"""
    return sum(2 if east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _normalize_color(value: str) -> str | None:
    """把 #RGB/#RRGGBB/常见英文色名规整为 openpyxl 的 ARGB 字符串，未知值返回 None。"""
    value = value.strip()
    if not _COLOR_RE.match(value):
        return None
    named = {
        "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000",
        "blue": "0000FF", "gray": "808080", "grey": "808080", "yellow": "FFFF00",
        "orange": "FFA500", "#333": "333333", "#fff": "FFFFFF", "#000": "000000",
    }
    hex6 = named.get(value.lower())
    if hex6 is None and value.startswith("#"):
        raw = value[1:]
        hex6 = raw if len(raw) == 6 else "".join(ch * 2 for ch in raw)
    if hex6 is None:
        return None
    return ("FF" + hex6.upper())


@dataclass
class BorderStyle:
    """单边框样式，``style=None`` 表示显式无边框。"""

    style: str | None = "thin"
    color: str | None = "FF000000"


@dataclass
class CellStyle:
    """单元格样式模型。字段为 ``None`` 表示"未指定"，合成时被更高优先级覆盖。"""

    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    font_name: str | None = None
    font_size_pt: float | None = None
    font_color: str | None = None
    fill_color: str | None = None
    halign: str | None = None
    valign: str | None = None
    wrap_text: bool | None = None
    border_top: BorderStyle | None = None
    border_bottom: BorderStyle | None = None
    border_left: BorderStyle | None = None
    border_right: BorderStyle | None = None
    width_percent: float | None = None

    def merge(self, override: "CellStyle") -> "CellStyle":
        """用 ``override`` 中的非 None 字段覆盖当前值，返回新对象（不可变合成）。"""
        merged = CellStyle(**{k: v for k, v in self.__dict__.items()})
        for name, value in override.__dict__.items():
            if value is not None:
                setattr(merged, name, value)
        return merged

    def with_grid_borders(self, side: BorderStyle) -> "CellStyle":
        """四边补上指定边框（已有显式边框的边不动）。"""
        return replace(
            self,
            border_top=self.border_top or side,
            border_bottom=self.border_bottom or side,
            border_left=self.border_left or side,
            border_right=self.border_right or side,
        )


@dataclass
class ImageAnchor:
    """浮动图片锚定描述（行列号 1 起，偏移与尺寸单位 px）。"""

    row: int
    col: int
    width_px: int
    height_px: int
    offset_x_px: int = 0
    offset_y_px: int = 0


# 标签语义默认（th 表头、行内强调），在解析期与 class 映射合成
def _tag_default(name: str) -> CellStyle | None:
    if name == "th":
        return CellStyle(bold=True, halign="center", valign="center")
    if name in ("b", "strong"):
        return CellStyle(bold=True)
    if name in ("i", "em"):
        return CellStyle(italic=True)
    if name == "u":
        return CellStyle(underline=True)
    if name in ("h1", "h2", "h3"):
        return CellStyle(bold=True)
    return None


@dataclass
class Html2XlsxProfile:
    """转换规则集：业务方通过它把"class 语义"注入通用引擎。

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
        row_height_factor: 行高系数（行高 ≈ 行数 × 字号pt × 该系数 + 固定余量）。
        landscape: 打印方向；False=A4 纵向。
        table_column_widths: 按表类指定各列宽百分比（如 ``{"sign-table": (38, 38, 24)}``），
            优先于首行 ``width%`` 行内样式。
    """

    container_class: str | None = None
    class_styles: dict[str, CellStyle] = field(default_factory=dict)
    full_width_border: bool = True
    column_layouts: dict[str, tuple[float, ...]] = field(default_factory=dict)
    table_style_rule: Callable[[str | None, int, int, CellStyle, int], CellStyle] | None = None
    image_rule: Callable[[Any, tuple], ImageAnchor | None] | None = None
    total_width_chars: float = _DEFAULT_TOTAL_WIDTH_CHARS
    row_height_factor: float = 1.35
    landscape: bool = False
    table_column_widths: dict[str, tuple[float, ...]] = field(default_factory=dict)


@dataclass
class Html2XlsxResult:
    """转换结果摘要。"""

    output_path: Path
    rows: int
    cols: int
    merges: int
    images: int
    warnings: list[str] = field(default_factory=list)


def _classes_of(node: Any) -> list[str]:
    """取节点的 class 列表，文本节点返回空表。

    兼容 beautifulsoup4 新旧版本：4.13+ 的多值属性返回列表，旧版返回空格分隔字符串。
    """
    if not hasattr(node, "get"):
        return []
    value = node.get("class")
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(item) for item in value]


def _ancestor_has_class(node: Any, cls: str, root: Any) -> bool:
    """判断 node 是否有带指定 class 的祖先（不越过 root）。"""
    parent = node.parent
    while parent is not None and parent is not root:
        if cls in _classes_of(parent):
            return True
        parent = parent.parent
    return False


def _matching_class_styles(node: Any, root: Any, profile: Html2XlsxProfile) -> list[CellStyle]:
    """收集命中的 class 映射，按特异性（作用域深度）升序返回。"""
    classes = _classes_of(node)
    hits: list[tuple[int, CellStyle]] = []
    for key, style in profile.class_styles.items():
        tokens = key.split()
        if not tokens:
            continue
        target = tokens[-1]
        matched = (target.startswith(".") and target[1:] in classes) or (not target.startswith(".") and node.name == target)
        if not matched:
            continue
        scope_ok = all(
            _ancestor_has_class(node, tok[1:], root) if tok.startswith(".") else True
            for tok in tokens[:-1]
        )
        if scope_ok:
            hits.append((len(tokens), style))
    hits.sort(key=lambda item: item[0])
    return [style for _, style in hits]


def _parse_inline_style(node: Any, style: CellStyle, warnings: list[str]) -> CellStyle:
    """把行内 ``style`` 属性中引擎关心的子集解析进样式（行内优先级最高）。"""
    raw = node.get("style") if hasattr(node, "get") else None
    if not raw:
        return style
    for decl in raw.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if prop == "text-align" and value in ("left", "right", "center"):
            style = replace(style, halign=value)
        elif prop == "vertical-align" and value in ("top", "middle", "bottom"):
            style = replace(style, valign="center" if value == "middle" else value)
        elif prop in ("font-weight",) and value in ("bold", "700", "800", "900"):
            style = replace(style, bold=True)
        elif prop == "font-size" and value.endswith("px"):
            try:
                style = replace(style, font_size_pt=float(value[:-2]) * _PX_TO_PT)
            except ValueError:
                warnings.append(f"无法解析 font-size: {value}")
        elif prop in ("color",) and (argb := _normalize_color(value)):
            style = replace(style, font_color=argb)
        elif prop in ("background", "background-color") and (argb := _normalize_color(value)):
            style = replace(style, fill_color=argb)
        elif prop == "white-space" and value == "pre-line":
            style = replace(style, wrap_text=True)
        elif prop in ("width",) and value.endswith("%"):
            try:
                style = replace(style, width_percent=float(value[:-1]))
            except ValueError:
                warnings.append(f"无法解析 width: {value}")
        elif prop == "border" or prop in _BORDER_SIDE_PROPS:
            style = _apply_border_decl(prop, value, style)
    return style


def _apply_border_decl(prop: str, value: str, style: CellStyle) -> CellStyle:
    """解析 ``border[-position]`` 声明（含 ``none``）到对应边。"""
    sides = list(_BORDER_SIDE_PROPS) if prop == "border" else [prop]
    if value.strip() in ("none", "0"):
        return replace(style, **{f"border_{side[7:]}": BorderStyle(style=None) for side in sides})
    match = _BORDER_RE.match(value)
    if not match:
        return style
    color = _normalize_color(match.group(2)) or "FF000000"
    side = BorderStyle(style="thin", color=color)
    new = {f"border_{s}": side for s in sides}
    return replace(style, **new)


def _resolve_style(node: Any, root: Any, profile: Html2XlsxProfile, warnings: list[str]) -> CellStyle:
    """三层合成：标签默认 → class 映射 → 行内样式。"""
    style = _tag_default(node.name) or CellStyle()
    for hit in _matching_class_styles(node, root, profile):
        style = style.merge(hit)
    return _parse_inline_style(node, style, warnings)


def _first_class(node: Any) -> str | None:
    classes = _classes_of(node) if node is not None else []
    return classes[0] if classes else None


class _Renderer:
    """单次转换的渲染上下文（openpyxl 依赖在构造时经 modutil 懒加载）。"""

    def __init__(self, profile: Html2XlsxProfile, images: dict[str, bytes]) -> None:
        self.profile = profile
        self.image_bytes = images
        self.warnings: list[str] = []
        self.pending_images: list[tuple[bytes, ImageAnchor]] = []
        self._col_widths: dict[int, float] = {}
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
        self.grid_width = 10
        self.max_table_cols = 0
        self.last_content_row = 0

    # ---------- 对外主流程 ----------

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
        for child in [c for c in root.children if getattr(c, "name", None)]:
            self._render_block(child, root)
        self._flush_images()
        self._apply_page_setup()
        self.workbook.save(str(output_path))
        return Html2XlsxResult(
            output_path=output_path,
            rows=self.last_content_row,
            cols=self.max_table_cols or self.grid_width,
            merges=len(self.sheet.merged_cells.ranges),
            images=len(self.pending_images),
            warnings=self.warnings,
        )

    # ---------- 块级渲染 ----------

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
        """多栏容器：各子块按相对宽度摆到并排的列段（Excel 无 flex 的近似摆法）。"""
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
            span = max(2, round(self.grid_width * ratio / total))
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
        span = col_span or self.grid_width
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
        # 行高：按换行数与显示宽度估算
        seg_lines = sum(self._lines_needed(seg, span) for seg in text.split("\n"))
        font_pt = style.font_size_pt or 9.0
        self.sheet.row_dimensions[r].height = max(14.0, seg_lines * font_pt * self.profile.row_height_factor + 4)
        self.last_content_row = max(self.last_content_row, r)
        self.row += 1

    def _lines_needed(self, text: str, span: int) -> int:
        usable = max(4.0, span * self._avg_col_chars() * 0.95)
        return max(1, -(-_display_width(text) // int(usable)))

    def _avg_col_chars(self) -> float:
        return self.profile.total_width_chars / max(1, self.grid_width)

    # ---------- 表格渲染 ----------

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
                text = cell_node.get_text("\n", strip=True) if cell_node.find(["p", "div", "li", "table"]) else _block_text(cell_node).strip()
                cells.append({"r": r_idx, "c": c_cursor, "cs": cs, "rs": rs, "style": style,
                              "text": text, "node": cell_node})
                for rr in range(rs):
                    for cc in range(cs):
                        occupied[(r_idx + rr, c_cursor + cc)] = "x"
                c_cursor += cs
                max_cols = max(max_cols, c_cursor)
        total_rows = (max((r for r, _ in occupied), default=-1) + 1) if occupied else 0
        self.grid_width = max(self.grid_width, max_cols)
        self.max_table_cols = max(self.max_table_cols, max_cols)
        # 写单元格：跨格先写值再合并，样式应用到合并区每个成员格（边框才能完整显示）
        for cell in cells:
            r0, c0, cs, rs, style = cell["r"], cell["c"], cell["cs"], cell["rs"], cell["style"]
            base_row = self.row + r0
            value = cell["text"] or None
            top_left = self.sheet.cell(row=base_row, column=c0 + 1, value=value)
            self._apply_style(top_left, style)
            for rr in range(rs):
                for cc in range(cs):
                    self._apply_style(self.sheet.cell(row=base_row + rr, column=c0 + 1 + cc), style)
            self.last_content_row = max(self.last_content_row, base_row + rs - 1)
            if cs > 1 or rs > 1:
                self.sheet.merge_cells(
                    start_row=base_row, start_column=c0 + 1,
                    end_row=base_row + rs - 1, end_column=c0 + cs,
                )
            td_node = cell["node"]
            if td_node.find("img"):
                self._anchor_images_of(td_node, ("table", table_cls, r0, c0),
                                       row=base_row, col=c0 + 1)
        # 列宽：profile 指定优先；否则首行单元格 width% 分摊总预算，未标注的列均摊剩余
        self._apply_column_widths(rows[0], max_cols, table_cls)
        # 行高
        for r_idx in range(total_rows):
            sheet_r = self.row + r_idx
            lines = 1
            font_pt = 9.0
            for cell in cells:
                if cell["r"] <= r_idx < cell["r"] + cell["rs"]:
                    segs = str(cell["text"]).split("\n") if cell["text"] else [""]
                    span_chars = sum(self._col_chars(c) for c in range(cell["c"], cell["c"] + cell["cs"]))
                    seg_lines = sum(max(1, -(-_display_width(seg) // max(4, int(span_chars * 0.95)))) for seg in segs)
                    lines = max(lines, seg_lines)
                    font_pt = max(font_pt, cell["style"].font_size_pt or 9.0)
            self.sheet.row_dimensions[sheet_r].height = max(
                14.0, lines * font_pt * self.profile.row_height_factor + 3)
        self.row += total_rows + 1  # 表格后空一行

    def _col_chars(self, col_idx: int) -> float:
        return self._col_widths.get(col_idx, self.profile.total_width_chars / max(1, self.grid_width))

    def _apply_column_widths(self, first_row: Any, max_cols: int, table_cls: str | None) -> None:
        """同 sheet 的列宽是全局共享的：先渲染的表格先占位（first-wins），后表不回写已占列。"""
        profile_widths = self.profile.table_column_widths.get(table_cls) if table_cls else None
        if profile_widths:
            for c in range(max_cols):
                if c in self._col_widths:
                    continue
                pct = profile_widths[c] if c < len(profile_widths) else 100.0 / max_cols
                self._col_widths[c] = self.profile.total_width_chars * pct / 100
        else:
            percents: dict[int, float] = {}
            c_cursor = 0
            for cell_node in first_row.find_all(["td", "th"], recursive=False):
                while c_cursor in percents or c_cursor in self._col_widths:
                    c_cursor += 1
                raw = cell_node.get("style", "")
                match = re.search(r"width\s*:\s*([\d.]+)\s*%", raw)
                if match:
                    percents[c_cursor] = float(match.group(1))
                c_cursor += int(cell_node.get("colspan", 1) or 1)
            pct_sum = sum(percents.values())
            budget = self.profile.total_width_chars
            unsized = [c for c in range(max_cols) if c not in self._col_widths]
            new_cols = [c for c in unsized if c not in percents]
            remaining_chars = budget * (100 - pct_sum) / 100 if pct_sum < 100 else 0.0
            for c in unsized:
                if c in percents:
                    self._col_widths[c] = budget * percents[c] / 100
                elif new_cols:
                    self._col_widths[c] = remaining_chars / len(new_cols)
                else:
                    self._col_widths[c] = budget / max_cols
        for c, chars in self._col_widths.items():
            letter = self.utils.get_column_letter(c + 1)
            self.sheet.column_dimensions[letter].width = round(max(4.0, chars), 2)

    # ---------- 图片 ----------

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

    # ---------- 样式落格与页面 ----------

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


def convert_html_to_xlsx(
    html: str,
    output_path: str | Path,
    *,
    profile: Html2XlsxProfile | None = None,
    sheet_name: str | None = None,
    images: dict[str, bytes] | None = None,
) -> Html2XlsxResult:
    """把 HTML 转写为带样式的 xlsx 文件。

    Args:
        html: 语义 HTML 字符串（带 class/行内样式，无 ``<style>`` 块）。
        output_path: 输出 xlsx 路径。
        profile: 转换规则集；None 时使用通用默认（全线框表格 + 无多栏规则）。
        sheet_name: 工作表名，默认 Sheet1。
        images: ``<img src>`` 到图片字节数据的映射，引擎不联网下载。

    Returns:
        :class:`Html2XlsxResult`：行列数、合并数、图片数与降级警告清单。

    Raises:
        ImportError: bs4/openpyxl 依赖自动安装失败时抛出。
        OSError: 输出路径不可写时抛出。
    """
    renderer = _Renderer(profile or Html2XlsxProfile(), images or {})
    return renderer.convert(html, Path(output_path), sheet_name)

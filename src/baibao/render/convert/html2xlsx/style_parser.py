"""
html2xlsx 样式解析器。

回答"一个 HTML 节点最终应用什么单元格样式"，三层合成：
    1. 标签语义默认（th 居中加粗、b/strong 加粗、i/em 斜体等）；
    2. profile 的 class 映射（支持 ``.cls``/``tag``/``.scope .cls`` 选择器，按特异性升序合并）；
    3. 行内 ``style`` 属性（CSS 白名单子集，优先级最高）。
解析失败的降级一律记入 warnings，不做静默丢弃。
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from baibao.office.excel import BorderStyle, CellStyle

from .model import Html2XlsxProfile

# region ======== 常量与正则 ========
# px → pt（CSS 96dpi 约定）
_PX_TO_PT = 0.75

# 行内样式属性的白名单解析（白名单之外的属性记"忽略"警告）
_COLOR_RE = re.compile(r"^(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})|[a-zA-Z]+)$")
_BORDER_RE = re.compile(r"^\s*(\d+)px\s+\w+\s+(#\w+|[a-zA-Z]+)\s*$")
_BORDER_SIDE_PROPS = ("border-top", "border-bottom", "border-left", "border-right")
# endregion


# region ======== 标签默认与 class 匹配 ========
def _tag_default(name: str) -> CellStyle | None:
    """
    标签语义默认样式（th 表头居中加粗、行内强调、标题加粗），解析期与 class 映射合成。
    """
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


def _classes_of(node: Any) -> list[str]:
    """
    取节点的 class 列表，文本节点返回空表。

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


def _first_class(node: Any) -> str | None:
    """
    取节点第一个 class，无 class 返回 None。
    """
    classes = _classes_of(node) if node is not None else []
    return classes[0] if classes else None


def _ancestor_has_class(node: Any, cls: str, root: Any) -> bool:
    """
    判断 node 是否有带指定 class 的祖先（不越过 root）。
    """
    parent = node.parent
    while parent is not None and parent is not root:
        if cls in _classes_of(parent):
            return True
        parent = parent.parent
    return False


def _matching_class_styles(node: Any, root: Any, profile: Html2XlsxProfile) -> list[CellStyle]:
    """
    收集命中的 class 映射，按特异性（作用域深度）升序返回。
    """
    classes = _classes_of(node)
    hits: list[tuple[int, CellStyle]] = []
    for key, style in profile.class_styles.items():
        tokens = key.split()
        if not tokens:
            continue
        target = tokens[-1]
        is_class = target.startswith(".")
        matched = (is_class and target[1:] in classes) or (not is_class and node.name == target)
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
# endregion


# region ======== 行内样式解析 ========
def _normalize_color(value: str) -> str | None:
    """
    把 #RGB/#RRGGBB/常见英文色名规整为 openpyxl 的 ARGB 字符串，未知值返回 None。
    """
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
    return "FF" + hex6.upper()


def _parse_inline_style(node: Any, style: CellStyle, warnings: list[str]) -> CellStyle:
    """
    解析行内 ``style`` 属性进样式，行内优先级最高。

    只认白名单子集：认识的属性值非法记"无法解析"警告，白名单之外的属性记"忽略"
    警告，不做静默丢弃。
    """
    raw = node.get("style") if hasattr(node, "get") else None
    if not raw:
        return style
    for decl in raw.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if not prop or not value:
            continue

        # 对齐类
        if prop == "text-align":
            if value in ("left", "right", "center"):
                style = replace(style, halign=value)
            else:
                warnings.append(f"无法解析 text-align: {value}")
        elif prop == "vertical-align":
            if value in ("top", "middle", "bottom"):
                style = replace(style, valign="center" if value == "middle" else value)
            else:
                warnings.append(f"无法解析 vertical-align: {value}")

        # 字体类
        elif prop == "font-weight":
            if value in ("bold", "700", "800", "900"):
                style = replace(style, bold=True)
            else:
                warnings.append(f"无法解析 font-weight: {value}（仅支持 bold/700/800/900）")
        elif prop == "font-size":
            if value.endswith("px"):
                try:
                    style = replace(style, font_size_pt=float(value[:-2]) * _PX_TO_PT)
                except ValueError:
                    warnings.append(f"无法解析 font-size: {value}")
            else:
                warnings.append(f"无法解析 font-size: {value}（仅支持 px）")
        elif prop == "color":
            if argb := _normalize_color(value):
                style = replace(style, font_color=argb)
            else:
                warnings.append(f"无法解析颜色: {value}（支持 #hex 与常见英文色名）")

        # 背景与布局类
        elif prop in ("background", "background-color"):
            if argb := _normalize_color(value):
                style = replace(style, fill_color=argb)
            else:
                warnings.append(f"无法解析颜色: {value}（支持 #hex 与常见英文色名）")
        elif prop == "white-space":
            if value == "pre-line":
                style = replace(style, wrap_text=True)
            else:
                warnings.append(f"无法解析 white-space: {value}（仅支持 pre-line）")
        elif prop == "width":
            if value.endswith("%"):
                try:
                    style = replace(style, width_percent=float(value[:-1]))
                except ValueError:
                    warnings.append(f"无法解析 width: {value}")
            else:
                warnings.append(f"无法解析 width: {value}（仅支持百分比）")
        elif prop == "border" or prop in _BORDER_SIDE_PROPS:
            style = _apply_border_decl(prop, value, style, warnings)

        # 白名单之外：无 Excel 对应物，记警告忽略
        else:
            warnings.append(f"忽略无 Excel 对应物的 CSS 属性: {prop}")
    return style


def _apply_border_decl(prop: str, value: str, style: CellStyle, warnings: list[str]) -> CellStyle:
    """
    解析 ``border[-position]`` 声明（含 ``none``）到对应边。

    ``none``/``0`` 显式去边框；其余按 ``Npx <样式> <颜色>`` 解析，语序不符记警告。
    """
    sides = list(_BORDER_SIDE_PROPS) if prop == "border" else [prop]
    if value.strip() in ("none", "0"):
        return replace(style, **{f"border_{side[7:]}": BorderStyle(style=None) for side in sides})
    match = _BORDER_RE.match(value)
    if not match:
        warnings.append(f"无法解析 {prop}: {value}（支持 'Npx solid <颜色>' 形式）")
        return style
    color = _normalize_color(match.group(2)) or "FF000000"
    side = BorderStyle(style="thin", color=color)
    new = {f"border_{s}": side for s in sides}
    return replace(style, **new)


def _resolve_style(node: Any, root: Any, profile: Html2XlsxProfile, warnings: list[str]) -> CellStyle:
    """
    三层合成：标签默认 → class 映射 → 行内样式。
    """
    style = _tag_default(node.name) or CellStyle()
    for hit in _matching_class_styles(node, root, profile):
        style = style.merge(hit)
    return _parse_inline_style(node, style, warnings)
# endregion

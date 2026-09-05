"""
HTML 报告片段工具集。

提供面向报告场景的 HTML 片段构建函数，包括表格、柱状图、折线图等。
所有函数均为纯函数，输出内容均经过 HTML 转义以防止 XSS。
"""

import datetime
import html
from collections.abc import Callable
from typing import Any

from pykunlun.util import objutil

from baibao.data import currency as currency_mod
from baibao.data.meta import Field, Style

# 默认柱状图颜色
DEFAULT_BAR_COLOR = '#4472c4'

# 折线图 SVG 布局常量
_SVG_WIDTH = 900
_SVG_HEIGHT = 340
_SVG_PAD_L = 90
_SVG_PAD_R = 40
_SVG_PAD_T = 30
_SVG_PAD_B = 50
_SVG_GRID_STEPS = 5
_SVG_LABEL_W = 110
_SVG_LABEL_LINE_H = 13


def _esc(value: Any) -> str:
    """
    转义 HTML 文本节点内容，防止 XSS 与页面错乱。

    仅转义 ``&`` ``<`` ``>`` 三个字符，不转义引号（适合放在标签之间的文本）。
    """
    return html.escape(str(value), quote=False)


def _esc_attr(value: Any) -> str:
    """
    转义 HTML 属性值，防止属性注入。

    在 ``_esc`` 基础上额外转义引号，适合放在 ``title="..."``、``style="..."`` 等属性中。
    """
    return html.escape(str(value), quote=True)


def _empty_html(title: str) -> str:
    """
    生成无数据时的空状态 HTML。

    Args:
        title: 区块标题文本（会被转义）

    Returns:
        形如 ``<h3>{title}</h3><p class="empty">暂无数据</p>`` 的字符串
    """
    return f'<h3>{_esc(title)}</h3><p class="empty">暂无数据</p>'


def _chart_header(title: str, legend_items: list[tuple[str, str | None]]) -> str:
    """
    构建图表容器头部（标题 + 图例）。

    Args:
        title: 图表标题文本（会被转义）
        legend_items: 图例项列表，每项为 ``(label, color)`` 二元组：

            - ``label``：系列名称（会被转义）
            - ``color``：颜色值；为 ``None`` 时使用 CSS class ``income-color``，
              为空列表时不输出图例区块

    Returns:
        图表头部的 HTML 片段（不含闭合标签，由调用方继续拼接 wrapper 与闭合）
    """
    parts = [f'<div class="chart-container"><h3>{_esc(title)}</h3>']
    if legend_items:
        parts.append('<div class="chart-legend">')
        for label, color in legend_items:
            if color:
                color_span = f'<span class="legend-color" style="background:{_esc_attr(color)}"></span>'
            else:
                color_span = '<span class="legend-color income-color"></span>'
            parts.append(f'<span class="legend-item">{color_span}{_esc(label)}</span>')
        parts.append('</div>')
    return ''.join(parts)


def _field_color(field: Field) -> str:
    """
    从 ``Field.style.color`` 读取颜色，缺省时返回 ``DEFAULT_BAR_COLOR``。

    Args:
        field: 字段元信息（允许为 ``None``，此时返回默认色）
    """
    style = objutil.get_attr(field, 'style', None)
    color = (style.color if style else None)
    return color or DEFAULT_BAR_COLOR


def _get_currency_symbol(field: Field, data: dict[str, Any]) -> str:
    """
    根据 Field 的币种配置和行数据解析货币符号。

    解析优先级：

        1. ``currency_field``：从行数据中动态读取币种字段的值；
        2. ``currency_value``：使用字段配置中的固定币种值。

    当币种在内置表中查不到对应符号时，**回落为币种 code 本身**（如 ``XYZ``），
    以保证信息不丢失。未配置任何币种时返回空串。

    Args:
        field: 字段元信息
        data: 当前行数据字典

    Returns:
        货币符号字符串；未配置币种时返回空串
    """
    cur_field = objutil.get_attr(field, 'currency_field', None)
    cur_value = objutil.get_attr(field, 'currency_value', None)
    if cur_field:
        code = str(data.get(cur_field, '') or '')
        return currency_mod.get_symbol_by_code(code, code)
    if cur_value:
        code = str(cur_value)
        return currency_mod.get_symbol_by_code(code, code)
    return ''


def create_style(color: str = '', **kwargs: Any) -> Style:
    """
    模板辅助：创建 Style 对象（便于链式配置字段样式）。

    Args:
        color: 颜色值，如 ``'#4e79a7'``、``'red'``；为空则不设置
        **kwargs: 其他 ``Style`` 支持的属性

    Returns:
        新建的 Style 对象
    """
    return Style(color=color, **kwargs) if color else Style(**kwargs)


def create_field(name: str, display_name: str = '', is_currency: bool = False,
                 currency_field: str = '', currency_value: str = '',
                 style: Style | None = None) -> Field:
    """
    模板辅助：创建 Field 对象，供 ``topn_single_bar_chart`` 等函数使用。

    Args:
        name: 数据键名
        display_name: 显示名称（图例 / 列头标签），默认同 ``name``
        is_currency: 是否为货币字段（影响数值是否拼接货币符号）
        currency_field: 数据中币种字段名（如 ``'settle_currency'``），动态读取
        currency_value: 固定币种值（如 ``'USD'``），与 ``currency_field`` 二选一
        style: Style 对象，控制颜色等样式

    Returns:
        新建的 Field 对象
    """
    return Field(
        name=name,
        display_name=display_name or name,
        is_currency=is_currency,
        currency_field=currency_field or None,
        currency_value=currency_value or None,
        style=style,
    )


def format_value(data: dict[str, Any], field: Field, default: Any = 0) -> str:
    """
    根据 Field 元信息格式化数值（货币或普通数字）。

    支持两种币种指定方式：

        - ``currency_field``：从 row 中动态读取币种字段获取符号；
        - ``currency_value``：使用固定币种值获取符号。

    当字段缺失或值为 ``None`` 时，使用 ``default`` 兜底：

        - 图表场景建议保持默认 ``0``（避免 ``max`` 等计算异常）；
        - 表格场景建议传 ``''``，使缺失单元格显示为空白。

    Args:
        data: 一行数据字典
        field: 字段元信息，包含 name / is_currency / currency_field / currency_value
        default: 字段缺失或值为 None 时的兜底值

    Returns:
        格式化后的字符串，例如 ``"¥ 1,234.56"``、``"1,234.56"`` 或 ``""``
    """
    key = objutil.get_attr(field, 'name', None)
    value = data.get(key, default) if key else default
    if value is None:
        value = default
    if isinstance(value, float):
        formatted = f'{value:,.2f}'
    elif isinstance(value, int):
        formatted = f'{value:,}'
    else:
        return str(value)
    if objutil.get_attr(field, 'is_currency', False):
        sym = _get_currency_symbol(field, data)
        return f'{sym} {formatted}'
    return formatted


def table_html(
    title: str,
    data_list: list[dict[str, Any]],
    headers: dict[str, Field | None] | None = None
) -> str:
    """
    生成表格 HTML。

    功能：

        1. 自动识别单条记录（dict）或多条记录（list）；
        2. 无数据时返回空状态提示；
        3. 未指定 ``headers`` 时，自动使用第一行数据的所有键作为列头；
        4. 支持货币字段格式化（通过 ``Field.is_currency`` 等控制）；
        5. 缺失字段或值为 ``None`` 时单元格显示为空白。

    Args:
        title: 表格标题文本（会被转义）
        data_list: 表格数据行列表，或单条字典
        headers: 列头信息字典，键为字段名，值为 ``Field`` 对象（可为 ``None``）。
            ``Field`` 可选属性：``display_name``（列显示名）、``is_currency``、
            ``currency_field``（动态币种字段名）、``currency_value``（固定币种值）。
            为 ``None`` 时按普通列处理，仅用键名作为列头。

    Returns:
        完整的表格 HTML 字符串，外层包裹 ``<div class="table-section">``
    """
    # 统一 data_list 为列表格式
    if isinstance(data_list, dict):
        data_list = [data_list]
    if not data_list:
        return _empty_html(title)
    # 未指定 headers 时，使用第一行数据的键作为列头
    if not headers:
        headers = {k: None for k in data_list[0]}

    parts = [f'<div class="table-section"><h3>{_esc(title)}</h3>',
             '<table class="data-table"><thead><tr>']
    for key, field in headers.items():
        display = objutil.get_attr(field, 'display_name', None) or key
        parts.append(f'<th>{_esc(display)}</th>')
    parts.append('</tr></thead><tbody>')

    # 遍历每一行，复用 format_value 格式化单元格；缺失值显示空白
    for row in data_list:
        parts.append('<tr>')
        for key, field in headers.items():
            # field 为 None 时用 Field(name=key) 兜底，确保 format_value 能取到列对应数据
            field_meta = field if field is not None else Field(name=key)
            parts.append(f'<td>{_esc(format_value(row, field_meta, default=""))}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def topn_single_bar_chart(
    title: str,
    name_field: Field,
    value_field: Field,
    data_list: list[dict[str, Any]],
) -> str:
    """
    生成 TopN 单柱状图 HTML（水平条形图）。

    按 ``value_field`` 数值降序排列，展示排名前 N 的数据项。
    每条记录显示排名标签（TOP1 / TOP2 ...）、名称、进度条和格式化数值。
    支持正负值（取绝对值计算柱宽）。

    Args:
        title: 图表标题文本（会被转义）
        name_field: 名称字段，``Field.name`` 为数据字典中的键名
        value_field: 数值字段，``Field.name`` 为数据键名，
            ``display_name`` 为图例标签，
            ``is_currency`` / ``currency_field`` / ``currency_value`` 控制数值格式化
        data_list: 数据列表，每项为包含 name_field 与 value_field 对应键名的字典

    Returns:
        TopN 单柱状图 HTML 字符串，外层包裹 ``<div class="chart-container">``
    """
    if not data_list:
        return _empty_html(title)

    name_key = objutil.get_attr(name_field, 'name', '') or ''
    value_key = objutil.get_attr(value_field, 'name', '') or ''
    value_label = objutil.get_attr(value_field, 'display_name', '') or value_key

    # 取绝对值计算宽度，避免负值导致宽度异常
    max_value = max(abs(r.get(value_key, 0) or 0) for r in data_list) or 1

    parts = [_chart_header(title, [(value_label, None)]),
             '<div class="horizontal-chart-wrapper">']

    for idx, data in enumerate(data_list):
        rank = idx + 1
        name = data.get(name_key, '') or f'#{rank}'
        value = data.get(value_key, 0) or 0
        width_pct = min(abs(value) / max_value * 100, 100)
        formatted_value = format_value(data, value_field)

        parts.append(
            '<div class="horizontal-bar-row">'
            f'<span class="bar-rank">TOP{rank}</span>'
            f'<span class="bar-name" title="{_esc_attr(name)}">{_esc(name)}</span>'
            '<div class="bar-track-group">'
            '<div class="bar-track">'
            f'<div class="horizontal-bar income-bar" style="width:{width_pct}%"></div>'
            '</div>'
            '</div>'
            '<div class="bar-values">'
            f'<span class="value-item">{_esc(formatted_value)}</span>'
            '</div>'
            '</div>'
        )

    parts.append('</div></div>')
    return ''.join(parts)


def topn_multi_bar_chart(
    title: str,
    name_field: Field,
    value_fields: list[Field],
    data_list: list[dict[str, Any]],
) -> str:
    """
    生成 TopN 多柱状图 HTML（水平条形图，支持多系列对比）。

    每条记录可显示多个系列的柱子，支持正负值（取绝对值计算柱宽），
    每个系列独立配色和格式化。

    Args:
        title: 图表标题文本（会被转义）
        name_field: 名称字段，``Field.name`` 为数据字典中的键名
        value_fields: 数值字段列表，每项为 Field 对象：

            - ``name``：数据键名
            - ``display_name``：图例标签
            - ``style.color``：柱状图颜色（默认 ``DEFAULT_BAR_COLOR``）
            - ``is_currency`` / ``currency_field`` / ``currency_value``：控制数值格式化

        data_list: 数据列表，每项为包含 name_field 与各 value_field 对应键名的字典

    Returns:
        TopN 多柱状图 HTML 字符串，外层包裹 ``<div class="chart-container">``
    """
    if not data_list:
        return _empty_html(title)

    name_key = objutil.get_attr(name_field, 'name', '') or ''

    # 各系列独立计算最大绝对值，用于柱宽归一化
    max_values: dict[str, float] = {}
    for vf in value_fields:
        key = objutil.get_attr(vf, 'name', '') or ''
        max_values[key] = max(abs(r.get(key, 0) or 0) for r in data_list) or 1

    legend_items: list[tuple[str, str | None]] = []
    for vf in value_fields:
        key = objutil.get_attr(vf, 'name', '') or ''
        label = objutil.get_attr(vf, 'display_name', '') or key
        legend_items.append((label, _field_color(vf)))

    parts = [_chart_header(title, legend_items), '<div class="horizontal-chart-wrapper">']

    for idx, data in enumerate(data_list):
        rank = idx + 1
        name = data.get(name_key, '') or f'#{rank}'

        parts.append(
            '<div class="horizontal-bar-row">'
            f'<span class="bar-rank">TOP{rank}</span>'
            f'<span class="bar-name" title="{_esc_attr(name)}">{_esc(name)}</span>'
            '<div class="bar-track-group">'
        )

        formatted_values: list[str] = []
        for vf in value_fields:
            key = objutil.get_attr(vf, 'name', '') or ''
            label = objutil.get_attr(vf, 'display_name', '') or key
            color = _field_color(vf)
            value = data.get(key, 0) or 0
            width_pct = min(abs(value) / max_values[key] * 100, 100)
            formatted = format_value(data, vf)
            formatted_values.append(formatted)

            parts.append(
                '<div class="bar-track">'
                f'<div class="horizontal-bar" style="width:{width_pct}%;background:{_esc_attr(color)}" '
                f'title="{_esc_attr(label)}: {_esc_attr(formatted)}"></div>'
                '</div>'
            )

        parts.append('</div><div class="bar-values">')
        for fv in formatted_values:
            parts.append(f'<span class="value-item">{_esc(fv)}</span>')
        parts.append('</div></div>')

    parts.append('</div></div>')
    return ''.join(parts)


def multi_line_chart(
    title: str,
    rows: list[dict[str, Any]],
    value_fields: list[Field],
    x_field: str = 'month_label',
) -> str:
    """
    生成折线图 HTML（纯 SVG 实现，不依赖任何 JS 库）。

    功能：

        1. 自动计算 Y 轴范围（含 10% 顶部留白，负值时底部留 5% 余量）；
        2. 支持多系列折线对比，每系列独立配色；
        3. 绘制网格线、坐标轴、X 轴标签；
        4. 每个数据点显示圆点标记；
        5. 在数据点上方叠加组合标签，显示所有系列的格式化值；
        6. 首列标签自动右移，避免与 Y 轴标签重叠。

    Args:
        title: 图表标题文本（会被转义）
        rows: 数据行列表，每行为字典，包含 ``x_field`` 键及各 ``value_fields`` 对应的键
        value_fields: 数值字段列表，每项为 Field 对象：

            - ``name``：数据键名
            - ``display_name``：图例标签
            - ``style.color``：折线颜色（默认 ``DEFAULT_BAR_COLOR``）
            - ``is_currency`` / ``currency_field`` / ``currency_value``：控制数值格式化

        x_field: X 轴字段名，默认为 ``'month_label'``

    Returns:
        折线图 HTML 字符串，外层包裹 ``<div class="chart-container">``，内含 ``<svg>`` 元素
    """
    if not rows:
        return _empty_html(title)

    row_count = len(rows)
    chart_w = _SVG_WIDTH - _SVG_PAD_L - _SVG_PAD_R
    chart_h = _SVG_HEIGHT - _SVG_PAD_T - _SVG_PAD_B

    # 收集所有数值，计算 Y 轴范围
    all_vals: list[float] = []
    for vf in value_fields:
        key = objutil.get_attr(vf, 'name', '') or ''
        for r in rows:
            all_vals.append(r.get(key, 0) or 0)
    v_min = min(0, min(all_vals))
    v_max = max(all_vals) if all_vals else 1
    if v_max == v_min:
        v_max = v_min + 1
    # 顶部留 10% 空白，负值时底部留 5% 余量
    v_range = v_max - v_min
    v_max += v_range * 0.1
    if v_min < 0:
        v_min -= v_range * 0.05

    def x_pos(i: int) -> float:
        if row_count == 1:
            return _SVG_PAD_L + chart_w / 2
        return _SVG_PAD_L + i * chart_w / (row_count - 1)

    def y_pos(v: float) -> float:
        return _SVG_PAD_T + chart_h * (1 - (v - v_min) / (v_max - v_min))

    svg = [f'<svg viewBox="0 0 {_SVG_WIDTH} {_SVG_HEIGHT}" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">']

    # 网格线 + Y 轴刻度
    for i in range(_SVG_GRID_STEPS + 1):
        grid_y = _SVG_PAD_T + chart_h * i / _SVG_GRID_STEPS
        grid_value = v_max - (v_max - v_min) * i / _SVG_GRID_STEPS
        svg.append(f'<line x1="{_SVG_PAD_L}" y1="{grid_y}" x2="{_SVG_WIDTH - _SVG_PAD_R}" y2="{grid_y}" class="line-chart-grid"/>')
        svg.append(f'<text x="{_SVG_PAD_L - 8}" y="{grid_y + 4}" text-anchor="end" class="line-chart-label">{grid_value:,.2f}</text>')

    # 坐标轴
    svg.append(f'<line x1="{_SVG_PAD_L}" y1="{_SVG_PAD_T}" x2="{_SVG_PAD_L}" y2="{_SVG_HEIGHT - _SVG_PAD_B}" class="line-chart-axis"/>')
    svg.append(f'<line x1="{_SVG_PAD_L}" y1="{_SVG_HEIGHT - _SVG_PAD_B}" x2="{_SVG_WIDTH - _SVG_PAD_R}" y2="{_SVG_HEIGHT - _SVG_PAD_B}" class="line-chart-axis"/>')

    # X 轴标签
    for i, r in enumerate(rows):
        month_x = x_pos(i)
        label = r.get(x_field, '')
        svg.append(f'<text x="{month_x}" y="{_SVG_HEIGHT - _SVG_PAD_B + 20}" class="line-chart-month-label">{_esc(label)}</text>')

    # 第一步：预计算每个系列在各 x 位置的点 (point_x, point_y, value, formatted, color)
    series_points: dict[int, list[tuple[float, float, Any, str, str]]] = {}
    for field_idx, vf in enumerate(value_fields):
        key = objutil.get_attr(vf, 'name', '') or ''
        color = _field_color(vf)
        pts: list[tuple[float, float, Any, str, str]] = []
        for i, r in enumerate(rows):
            v = r.get(key, 0) or 0
            pts.append((x_pos(i), y_pos(v), v, format_value(r, vf), color))
        series_points[field_idx] = pts

    # 第二步：绘制面积填充、折线、圆点
    for pts in series_points.values():
        if not pts:
            continue
        color = pts[0][4]
        # 面积填充
        area_path = f'M{pts[0][0]},{_SVG_HEIGHT - _SVG_PAD_B}'
        for point_x, point_y, *_ in pts:
            area_path += f' L{point_x},{point_y}'
        area_path += f' L{pts[-1][0]},{_SVG_HEIGHT - _SVG_PAD_B} Z'
        svg.append(f'<path d="{area_path}" fill="{_esc_attr(color)}" class="line-chart-area"/>')
        # 折线
        polyline_pts = ' '.join(f'{point_x},{point_y}' for point_x, point_y, *_ in pts)
        svg.append(f'<polyline points="{polyline_pts}" fill="none" stroke="{_esc_attr(color)}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>')
        # 圆点
        for point_x, point_y, *_ in pts:
            svg.append(f'<circle cx="{point_x}" cy="{point_y}" r="4" fill="{_esc_attr(color)}" class="line-chart-dot"/>')

    # 第三步：按 x 位置重组标签数据 (field_idx, point_x, point_y, value, color, formatted)
    points_by_x: dict[int, list[tuple[int, float, float, Any, str, str]]] = {}
    for field_idx, pts in series_points.items():
        for i, (point_x, point_y, v, formatted, color) in enumerate(pts):
            points_by_x.setdefault(i, []).append((field_idx, point_x, point_y, v, color, formatted))

    # 第四步：绘制组合标签，每个 x 位置一个，显示所有系列的值
    for x_idx in range(row_count):
        if x_idx not in points_by_x:
            continue
        series_data = points_by_x[x_idx]
        # 取最高点（y 最小）定位标签
        top_y = min(d[2] for d in series_data)
        point_x = series_data[0][1]
        # 按系列顺序排列
        series_data.sort(key=lambda d: d[0])
        lines = [(color, formatted) for _, _, _, _, color, formatted in series_data]

        rect_height = len(lines) * _SVG_LABEL_LINE_H + 6
        rect_width = _SVG_LABEL_W
        rect_x = point_x - rect_width / 2
        rect_y = top_y - rect_height - 8
        # 首列右移，避免遮挡 Y 轴标签
        if x_idx == 0:
            rect_x = point_x + 8
        # 顶部越界时下移到点下方
        if rect_y < _SVG_PAD_T:
            rect_y = top_y + 12

        svg.append(f'<rect x="{rect_x}" y="{rect_y}" width="{rect_width}" height="{rect_height}" rx="4" fill="white" stroke="#ddd" stroke-width="0.8" opacity="0.5"/>')
        for line_idx, (color, txt) in enumerate(lines):
            text_y = rect_y + 12 + line_idx * _SVG_LABEL_LINE_H
            svg.append(f'<circle cx="{rect_x + 8}" cy="{text_y - 3.5}" r="3" fill="{_esc_attr(color)}"/>')
            svg.append(f'<text x="{rect_x + 15}" y="{text_y}" class="line-chart-value-label" fill="#333">{_esc(txt)}</text>')

    svg.append('</svg>')

    # 组装最终 HTML（图例 + SVG）
    legend_items: list[tuple[str, str | None]] = []
    for vf in value_fields:
        key = objutil.get_attr(vf, 'name', '') or ''
        label = objutil.get_attr(vf, 'display_name', '') or key
        legend_items.append((label, _field_color(vf)))
    parts = [_chart_header(title, legend_items),
             f'<div class="line-chart-wrapper">{"".join(svg)}</div></div>']
    return ''.join(parts)


def horizontal_single_bar_chart(
    title: str,
    data_list: list[dict[str, Any]],
    name_field: str,
    value_field: str,
    name_formatter: Callable[[dict[str, Any], str], str] | None = None,
    value_formatter: Callable[[dict[str, Any], int], str] | None = None,
    extra_fields: list[dict[str, Any]] | None = None,
) -> str:
    """
    生成通用水平柱状图 HTML。

    与 ``topn_single_bar_chart`` 不同，此函数不排序、不显示排名标签，
    直接按 ``data_list`` 顺序渲染，适合自定义排序场景。

    Args:
        title: 图表标题文本（会被转义）
        data_list: 数据列表，每项为字典
        name_field: 名称字段名（数据字典中的键）
        value_field: 数值字段名（用于计算柱状图宽度比例）
        name_formatter: 名称格式化函数，签名 ``(row, name_value) -> str``；
            为 ``None`` 时直接转为字符串
        value_formatter: 数值格式化函数，签名 ``(row, value) -> str``，
            用于 tooltip 显示；为 ``None`` 时使用千分位格式
        extra_fields: 右侧额外显示的字段列表，每项为字典：

            - ``field``：字段名
            - ``suffix``：后缀（如 ``'次'``、``'人'``）
            - ``formatter``：可选格式化函数，签名 ``(row, value) -> str``

    Returns:
        水平柱状图 HTML 字符串，外层包裹 ``<div class="chart-container">``
    """
    if not data_list:
        return _empty_html(title)

    max_value = max(data.get(value_field, 0) or 0 for data in data_list) or 1
    parts = [_chart_header(title, []), '<div class="chart-bar-wrapper">']

    for data in data_list:
        name_value = data.get(name_field, '')
        value = data.get(value_field, 0) or 0
        width_pct = value / max_value * 100

        # 格式化名称 / 数值（tooltip）
        label = name_formatter(data, name_value) if name_formatter else str(name_value)
        tooltip_text = value_formatter(data, value) if value_formatter else f'{value:,}'

        # 右侧额外显示字段
        extra_values: list[str] = []
        if extra_fields:
            for extra in extra_fields:
                field_name = extra.get('field', '')
                suffix = extra.get('suffix', '')
                formatter = extra.get('formatter')
                extra_value = data.get(field_name, 0) or 0
                if formatter:
                    extra_values.append(formatter(data, extra_value))
                else:
                    extra_values.append(f'{extra_value:,}{suffix}')

        right_text = ' '.join(extra_values) if extra_values else tooltip_text

        parts.append(
            '<div class="chart-bar-row">'
            f'<span class="chart-bar-label">{_esc(label)}</span>'
            '<div class="chart-bar-track">'
            f'<div class="chart-bar-fill" style="width:{width_pct}%">'
            f'<span class="chart-bar-tooltip">{_esc(tooltip_text)}</span>'
            '</div>'
            '</div>'
            f'<span class="chart-bar-value">{_esc(right_text)}</span>'
            '</div>'
        )

    parts.append('</div></div>')
    return ''.join(parts)


def hourly_distribution_bar_chart(
    title: str,
    data_list: list[dict[str, Any]],
    hour_field: str = 'hour',
    count_field: str = 'action_count'
) -> str:
    """
    生成时段分布柱状图 HTML。

    将数据按小时分组，展示一天 24 小时内各时段的活跃次数分布。
    内部复用 ``horizontal_single_bar_chart``，自动格式化小时标签（``HH:00``）
    与计数（``N 次``）。

    Args:
        title: 图表标题文本（会被转义）
        data_list: 数据列表，每项包含 ``hour`` 与 ``action_count`` 字段
        hour_field: 小时字段名，默认为 ``'hour'``
        count_field: 次数字段名，默认为 ``'action_count'``

    Returns:
        时段分布柱状图 HTML 字符串
    """
    def format_hour(row: dict[str, Any], value: Any) -> str:
        return f'{value:02d}:00'

    def format_count(row: dict[str, Any], value: Any) -> str:
        return f'{value:,} 次'

    return horizontal_single_bar_chart(
        title=title,
        data_list=data_list,
        name_field=hour_field,
        value_field=count_field,
        name_formatter=format_hour,
        value_formatter=format_count,
    )


def daily_trend_bar_chart(rows: list[dict[str, Any]], chart_title: str = '每日操作趋势') -> str:
    """
    生成每日趋势柱状图 HTML。

    展示每日的操作次数和独立用户数。内部复用 ``horizontal_single_bar_chart``，
    自动格式化日期标签（``MM月DD日``）、计数（``N 次``），
    并在右侧额外显示用户数（``N 人``）。

    Args:
        rows: 数据行列表，每项包含 ``action_date``（``YYYY-MM-DD``）、
            ``action_count``、``user_count`` 字段
        chart_title: 图表标题，默认为 ``'每日操作趋势'``

    Returns:
        每日趋势柱状图 HTML 字符串
    """
    def format_date(row: dict[str, Any], value: Any) -> str:
        date_str = str(value)[:10]
        try:
            dt = datetime.date.fromisoformat(date_str)
            return f'{dt.month}月{dt.day}日'
        except (ValueError, TypeError):
            return date_str

    def format_count(row: dict[str, Any], value: Any) -> str:
        return f'{value:,} 次'

    return horizontal_single_bar_chart(
        title=chart_title,
        data_list=rows,
        name_field='action_date',
        value_field='action_count',
        name_formatter=format_date,
        value_formatter=format_count,
        extra_fields=[{'field': 'user_count', 'suffix': ' 人'}],
    )

"""
指标卡片（Metric Card）渲染工具。

提供基于配置驱动的指标卡片网格渲染能力，常用于报表顶部的
"关键指标概览"区域。

核心概念：

    - :class:`MetricSpec`：单张卡片的渲染规则（标签、字段、格式类型、颜色）
    - :class:`MetricGroupSpec`：一组卡片的渲染规则（数据源、币种、布局）
    - :func:`render_metric_section`：渲染多个分组为完整 HTML 片段

输出 HTML 使用 ``metric-card`` / ``metric-grid`` / ``metric-{color}`` 等 CSS 类名，
需配合报表模板中对应的样式定义。

示例::

    specs = [
        MetricGroupSpec(
            title='订单概览',
            source_key='order_summary',
            currency_field='settle_currency',
            metrics=[
                MetricSpec('订单数', 'order_count', 'count', 'blue', '笔'),
                MetricSpec('总收汇', 'total_income', 'currency', 'green'),
            ],
        ),
    ]
    html = render_metric_section(specs, data)
"""

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from baibao.data import currency as currency_mod


# region ======== 基础格式化辅助 ========
def _esc(value: Any) -> str:
    """转义 HTML 文本节点内容（仅 ``&`` ``<`` ``>``），防止 XSS。"""
    return html.escape(str(value), quote=False)


def format_number(value: Any) -> str:
    """数值千分位格式化，保留两位小数。

    - ``None`` / 空字符串 → ``'-'``
    - 字符串数字 → 自动转换
    - 无法转换的值 → 原样字符串返回（避免渲染时崩溃）

    Args:
        value: 数值、字符串数字或其他任意值

    Returns:
        形如 ``"1,234.56"`` 的格式化字符串；无法转换时返回 ``str(value)``
    """
    if value is None or value == '':
        return '-'
    try:
        return f'{float(value):,.2f}'
    except (TypeError, ValueError):
        return str(value)


def format_percent(value: Any) -> str:
    """百分比格式化（输入已是百分数值，例如 ``12.3`` 表示 ``12.3%``）。

    Args:
        value: 百分数值（数字或字符串数字）

    Returns:
        形如 ``"12.3%"`` 的字符串；无法转换时返回 ``str(value)``
    """
    if value is None or value == '':
        return '-'
    try:
        return f'{float(value):.1f}%'
    except (TypeError, ValueError):
        return str(value)


def _resolve_currency_symbol(row: Mapping[str, Any],
                             currency_field: str,
                             currency_resolver: Callable[[Mapping[str, Any]], str] | None = None) -> str:
    """从行数据解析货币符号。

    解析优先级：

        1. ``currency_resolver``：自定义解析回调，适合"多字段优先级匹配"等业务规则；
        2. ``currency_field``：从行中读取该字段作为币种 code，再查表得符号。

    两种来源都未提供时返回空串；查不到对应符号时回落为 code 本身，
    以保证信息不丢失。
    """
    # 业务侧多字段优先级等复杂规则，交给回调处理
    if currency_resolver is not None:
        return currency_resolver(row)
    # 默认路径：直接读 currency_field 字段
    if currency_field:
        code = str(row.get(currency_field, '') or '')
        return currency_mod.get_symbol_by_code(code, code)
    return ''
# endregion


# region ======== 数据类 ========
@dataclass
class MetricSpec:
    """单张指标卡片的渲染规则。

    Args:
        label: 卡片标签文本（不含币种后缀，运行时会按需追加 ``(USD)`` 等）
        field: 数据字段名，从行数据中读取该键的值进行格式化
        format_type: 格式类型，取值：

            - ``'currency'``：货币，按千分位两位小数 + 币种符号
            - ``'count'``：计数，按千分位整数 + ``unit``
            - ``'percent'``：百分比，保留一位小数

        color: 颜色主题，对应 CSS 类 ``metric-{color}``（如 ``blue``/``green``）
        unit: 仅 ``format_type='count'`` 时使用的单位后缀（如 ``'次'``/``'人'``）
    """

    label: str
    field: str
    format_type: str
    color: str
    unit: str = ''


@dataclass
class MetricGroupSpec:
    """一组指标卡片的渲染规则。

    内置三种布局：

        - ``per_currency_grid``（默认）：每个币种独立一个 ``metric-grid``，
          适合多币种数据各自成块的场景；
        - ``flat``：所有行平铺在同一个 ``metric-grid``，
          适合单行或希望紧凑排列的场景；
        - ``first_row``：仅渲染第一行，适合聚合汇总数据（如总操作次数）。

    特殊场景可设置 ``renderer`` 回调完全自定义渲染逻辑
    （例如把多个数据源合并到同一网格），设置后忽略 ``layout``。

    Args:
        title: 分组标题，渲染为 ``metric-group-label``
        source_key: ``data`` 字典中对应的键名，其值为行列表；
            ``renderer`` 模式下可不填
        metrics: 卡片规则列表
        currency_field: 用于读取币种符号的字段名（如 ``'settle_currency'``）
        layout: 内置布局名称，见上方说明
        renderer: 自定义渲染器，签名 ``(data: Mapping) -> str``；
            设置后忽略 ``layout``，直接委托给它
    """

    title: str
    source_key: str = ''
    metrics: list[MetricSpec] = dc_field(default_factory=list[MetricSpec])
    currency_field: str = ''
    layout: str = 'per_currency_grid'
    renderer: Callable[[Mapping[str, Any]], str] | None = None
# endregion


# region ======== 渲染逻辑 ========
def _format_metric_value(spec: MetricSpec, row: Mapping[str, Any],
                         currency_symbol: str) -> str:
    """根据 :attr:`MetricSpec.format_type` 把行字段格式化为卡片显示文本。"""
    raw = row.get(spec.field, 0)
    if spec.format_type == 'currency':
        # 货币：符号 + 千分位金额（符号可能为空，此时只显示金额）
        return f'{currency_symbol} {format_number(raw)}'
    if spec.format_type == 'percent':
        return format_percent(raw)
    # count：千分位整数 + 单位后缀；非数字值原样返回避免渲染崩溃
    try:
        return f'{int(float(raw)):,} {spec.unit}'.rstrip()
    except (TypeError, ValueError):
        return f'{raw} {spec.unit}'.strip()


def _render_metric_cards(specs: Sequence[MetricSpec], row: Mapping[str, Any],
                         multi_currency: bool, currency_field: str,
                         currency_resolver: Callable[[Mapping[str, Any]], str] | None = None) -> str:
    """渲染一行数据对应的若干指标卡片。

    每张卡片共享本行的币种上下文：当 ``multi_currency`` 为真且配置了
    ``currency_field`` 时，会在标签后追加 ``(USD)`` 形式的币种 code，
    让用户一眼区分不同币种的卡片。
    """
    sym = _resolve_currency_symbol(row, currency_field, currency_resolver)
    cur_code = str(row.get(currency_field, '')) if currency_field else ''
    parts: list[str] = []
    for spec in specs:
        # 多币种场景下追加 code 后缀，单币种场景保持原标签
        if multi_currency and currency_field:
            label = f'{spec.label}({cur_code})'
        else:
            label = spec.label
        value = _format_metric_value(spec, row, sym)
        parts.append(
            f'    <div class="metric-card metric-{spec.color}">\n'
            f'      <div class="metric-label">{_esc(label)}</div>\n'
            f'      <div class="metric-value">{_esc(value)}</div>\n'
            f'    </div>\n'
        )
    return ''.join(parts)


def render_metric_group(spec: MetricGroupSpec, data: Mapping[str, Any],
                        currency_resolver: Callable[[Mapping[str, Any]], str] | None = None) -> str:
    """渲染一个指标分组为 HTML 片段。

    分派顺序：

        1. 若 :attr:`MetricGroupSpec.renderer` 不为 ``None``，直接委托给它
           （业务侧完全自定义，例如付款组合并国内/海外）；
        2. 否则按 :attr:`MetricGroupSpec.layout` 选择内置布局。

    Args:
        spec: 分组渲染规则
        data: 完整数据字典，从中按 ``spec.source_key`` 取行列表
        currency_resolver: 可选的币种符号解析回调，签名 ``(row) -> str``；
            为 ``None`` 时使用 ``spec.currency_field`` 单字段路径

    Returns:
        HTML 片段字符串；分组无数据时返回空串
    """
    # 自定义渲染器优先，替代内置 layout（如付款组的合并展示）
    if spec.renderer is not None:
        return spec.renderer(data)

    rows: list[Mapping[str, Any]] = data.get(spec.source_key, []) or []
    if not rows:
        return ''
    # first_row：聚合类数据（如总操作次数）只关心汇总行，截掉其余行
    if spec.layout == 'first_row':
        rows = rows[:1]

    multi = len(rows) > 1
    parts: list[str] = [f'  <div class="metric-group-label">{_esc(spec.title)}</div>\n']

    if spec.layout == 'per_currency_grid':
        # 每个币种独立一个 grid，避免不同币种的卡片混在同一行
        for row in rows:
            parts.append('  <div class="metric-grid">\n')
            parts.append(_render_metric_cards(spec.metrics, row, multi,
                                              spec.currency_field, currency_resolver))
            parts.append('  </div>\n')
    else:
        # flat：所有行平铺在同一个 grid，适合单行或紧凑排列
        parts.append('  <div class="metric-grid">\n')
        for row in rows:
            parts.append(_render_metric_cards(spec.metrics, row, multi,
                                              spec.currency_field, currency_resolver))
        parts.append('  </div>\n')
    return ''.join(parts)


def render_metric_section(specs: Sequence[MetricGroupSpec], data: Mapping[str, Any],
                          currency_resolver: Callable[[Mapping[str, Any]], str] | None = None) -> str:
    """渲染多个指标分组为 HTML 片段（即报表顶部的关键指标概览区）。

    Args:
        specs: 分组规则列表
        data: 完整数据字典
        currency_resolver: 可选的币种符号解析回调，透传给每个 :func:`render_metric_group`

    Returns:
        拼接所有分组后的 HTML 片段字符串
    """
    return ''.join(render_metric_group(spec, data, currency_resolver) for spec in specs)
# endregion

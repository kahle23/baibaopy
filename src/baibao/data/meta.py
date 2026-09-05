"""
元数据模块。

提供字段、表头、动态表单等元数据描述类。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Style:
    """
    样式。
    """
    # 主体颜色（主颜色，如 "#ff0000", "red"）
    color: str | None = None
    # 自定义样式字典，用于扩展其他样式属性
    custom: dict[str, Any] | None = field(default_factory=dict[str, Any])


@dataclass
class Field:
    """
    表头字段描述，用于定义动态表格等场景的列信息。

    支持字段名、展示名、货币类型等属性配置。
    """
    # 字段名
    name: str | None = None
    # 展示名
    display_name: str | None = None
    # 字段样式
    style: Style | None = None
    # 是否是货币字段
    is_currency: bool = False
    # 如果是货币字段，币种走哪个具体字段
    currency_field: str | None = None
    # 如果是货币字段，币种走当前字段的值（与 currency_field 二选一，优先 currency_field）
    currency_value: str | None = None


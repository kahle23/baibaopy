# data 模块使用指南

> 本文档详细记录 `baibao.data` 包中各子模块的使用方法。data 包聚焦于**数据定义**：货币查询与字段元数据。

## 目录

- [currency - 货币模块](#1-currency---货币模块)
- [meta - 元数据模块](#2-meta---元数据模块)

---

## 1. currency - 货币模块

提供币种数据对象 `Currency` 及相关查询、管理工具。内置人民币、美元、欧元、英镑、俄罗斯卢布、港币、新加坡元等常用货币。

### 1.1 数据结构

```python
from baibao.data import currency

# Currency 是 frozen dataclass，包含三个字段
# symbol: 符号，如 ￥、$
# code:   编码，如 CNY、USD
# name:   名字，如 人民币、美元
```

### 1.2 查询货币

```python
from baibao.data import currency

# 根据符号查询
c = currency.get_by_symbol("￥")
print(c)  # Currency(symbol='￥', code='CNY', name='人民币')

# 根据编码查询（忽略大小写）
c = currency.get_by_code("usd")
print(c)  # Currency(symbol='$', code='USD', name='美元')

# 根据名字查询
c = currency.get_by_name("欧元")
print(c)  # Currency(symbol='€', code='EUR', name='欧元')

# 智能搜索：依次匹配符号、编码、名字，返回第一个匹配项
c = currency.search_first("HK$")
c = currency.search_first("GBP")
c = currency.search_first("新加坡")
```

### 1.3 获取符号快捷方法

```python
from baibao.data import currency

# 根据编码获取符号
symbol = currency.get_symbol_by_code("CNY")       # "￥"
symbol = currency.get_symbol_by_code("USD")       # "$"
symbol = currency.get_symbol_by_code("UNKNOWN")   # ""（未找到返回默认值）
symbol = currency.get_symbol_by_code("UNKNOWN", default="N/A")  # "N/A"
symbol = currency.get_symbol_by_code("")          # ""（空输入直接返回默认值）
```

### 1.4 新增币种

```python
from baibao.data import currency

# 新增成功返回 True
ok = currency.add(symbol="₩", code="KRW", name="韩元")
print(ok)  # True

# 编码重复时返回 False
ok = currency.add(symbol="¥", code="CNY", name="人民币")
print(ok)  # False

# 参数为空时返回 False
ok = currency.add(symbol="", code="XXX", name="测试")
print(ok)  # False
```

### 1.5 删除币种

```python
from baibao.data import currency

# 删除成功返回 True
ok = currency.remove("KRW")
print(ok)  # True

# 编码不存在时返回 False
ok = currency.remove("UNKNOWN")
print(ok)  # False
```

---

## 2. meta - 元数据模块

提供字段（`Field`）与样式（`Style`）两类元数据描述，用于动态表格、报告渲染等场景的列定义。

```python
from baibao.data import Field, Style

# Style：样式描述（颜色 + 自定义属性）
style = Style(color="#ff0000")

# Field：表头字段描述
field = Field(
    name="amount",            # 数据键名
    display_name="金额",       # 展示名（列头 / 图例）
    is_currency=True,         # 是否为货币字段
    currency_field="cur",     # 币种走某字段动态读取（与 currency_value 二选一）
    style=style,              # 字段样式
)
```

`Field` 与 `Style` 主要配合 `baibao.render.html` 的表格、图表、指标卡片函数使用，详见 [render 模块文档](../output/render.md)。

---

## 综合示例

### 货币格式化

```python
from baibao.data import currency

def format_price(amount: float, code: str) -> str:
    """格式化价格，自动添加货币符号。"""
    symbol = currency.get_symbol_by_code(code, default=code)
    return f"{symbol}{amount:,.2f}"

print(format_price(1234.5, "CNY"))   # ￥1,234.50
print(format_price(99.99, "USD"))    # $99.99
print(format_price(500, "EUR"))      # €500.00
print(format_price(100, "UNKNOWN"))  # UNKNOWN100.00
```

# render 模块使用指南

> `baibao.render` 包聚焦于**内容渲染**，包含两个子模块：模板引擎（template）与 HTML 报告片段构建（html）。

## 目录

- [template - 模板引擎模块](#1-template---模板引擎模块)
- [html - HTML 报告片段模块](#2-html---html-报告片段模块)

---

## 1. template - 模板引擎模块

提供基于策略模式的模板引擎抽象，内置 Jinja2 实现。支持模板字符串 / 文件 / 流的渲染、自定义过滤器、全局变量、运行时切换引擎。

jinja2 库会在首次使用时自动安装。

### 1.1 渲染模板字符串

```python
from baibao.render.template import render_string_to_string

# 变量替换
result = render_string_to_string("Hello, {{ name }}!", name="World")
print(result)  # Hello, World!

# 过滤器与控制结构
result = render_string_to_string("{{ name | upper }}", name="hello")
print(result)  # HELLO
```

### 1.2 渲染模板文件

```python
from baibao.render.template import render_file_to_string, render_file_to_file

# 读取模板文件 → 返回字符串
result = render_file_to_string("templates/report.html", title="月度报告")

# 读取模板文件 → 写入另一个文件（流式，适合大文件）
render_file_to_file("templates/report.html", "output/report.html", title="月度报告")
```

### 1.3 字符串 → 文件

```python
from baibao.render.template import render_string_to_file

render_string_to_file(
    "<h1>{{ title }}</h1>",
    "output/title.html",
    title="标题",
)
```

### 1.4 自定义 Jinja2Engine

```python
from baibao.render.template import Jinja2Engine

engine = Jinja2Engine(
    template_dir="templates",   # 模板文件目录
    auto_escape=False,          # 禁用 HTML 自动转义
    undefined="undefined",      # 未定义变量返回空字符串（而非报错）
)

# 自定义过滤器
engine.add_filter("reverse", lambda s: s[::-1])

# 全局变量（所有模板可访问）
engine.add_global("site_name", "我的网站")

# 直接调用引擎实例渲染
result = engine.render_string_to_string("{{ name | reverse }}", name="Hello")
print(result)  # olleH
```

### 1.5 运行时切换引擎

```python
from baibao.render.template import (
    Jinja2Engine, set_template_engine, get_template_engine, render_string_to_string,
)

# 注册自定义引擎（按名称）
set_template_engine("custom", Jinja2Engine(auto_escape=False, undefined="undefined"))

# 指定使用某个引擎渲染
result = render_string_to_string("Hello, {{ name }}!", engine_name="custom", name="World")

# 取回引擎实例
engine = get_template_engine("custom")
```

### 1.6 Jinja2Engine 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `template_dir` | `str \| None` | `None` | 模板文件目录，为 None 时只能渲染字符串 |
| `auto_escape` | `bool` | `True` | 是否启用 HTML 自动转义 |
| `cache_size` | `int` | `400` | 模板缓存大小，0 禁用缓存 |
| `undefined` | `str` | `"strict"` | 未定义变量处理：`strict` 报错 / `undefined` 返回空 / `debug` 返回调试信息 |
| `filters` | `dict` | `{}` | 自定义过滤器字典 |
| `globals` | `dict` | `{}` | 全局变量字典 |

---

## 2. html - HTML 报告片段模块

提供面向报告场景的 HTML 片段构建函数（表格、柱状图、折线图、指标卡片）。所有输出均经过 HTML 转义以防止 XSS，纯函数无副作用，常配合 `template` 模块拼装完整报告。

> 字段元数据（`Field` / `Style`）来自 [`baibao.data`](../data/data.md#2-meta---元数据模块)。

### 2.1 表格

```python
from baibao.render.html import table_html
from baibao.data import Field

data = [
    {"name": "苹果", "amount": 12.5},
    {"name": "香蕉", "amount": 8.0},
]
headers = {"name": Field(name="name", display_name="品名"), "amount": None}

html_str = table_html("水果清单", data, headers)
```

### 2.2 柱状图

```python
from baibao.render.html import topn_single_bar_chart, topn_multi_bar_chart
from baibao.data import Field, Style

name_field = Field(name="region", display_name="地区")
value_field = Field(name="total", display_name="销售额", is_currency=True,
                    currency_value="CNY", style=Style(color="#4e79a7"))

# 单系列 TopN 水平条形图（按值降序，显示排名）
topn_single_bar_chart("销售额 TopN", name_field, value_field, data)

# 多系列对比（每个 Field 独立配色）
topn_multi_bar_chart("对比", name_field, [value_field, value_field2], data)
```

### 2.3 折线图（纯 SVG）

```python
from baibao.render.html import multi_line_chart
from baibao.data import Field

rows = [
    {"month_label": "1月", "income": 100, "cost": 60},
    {"month_label": "2月", "income": 120, "cost": 70},
]
multi_line_chart(
    "月度趋势",
    rows,
    [Field(name="income", display_name="收入"), Field(name="cost", display_name="成本")],
)
```

### 2.4 指标卡片

```python
from baibao.render.html import render_metric_section, MetricGroupSpec, MetricSpec

specs = [
    MetricGroupSpec(
        title="订单概览",
        source_key="order_summary",
        currency_field="settle_currency",
        metrics=[
            MetricSpec("订单数", "order_count", "count", "blue", "笔"),
            MetricSpec("总收汇", "total_income", "currency", "green"),
        ],
    ),
]
html_str = render_metric_section(specs, data)
```

`MetricSpec.format_type` 取值：`currency`（货币）、`count`（计数 + unit）、`percent`（百分比）。
`MetricGroupSpec.layout` 取值：`per_currency_grid`（默认，按币种分块）、`flat`（平铺）、`first_row`（仅首行汇总）。

### 2.5 函数一览

| 函数 | 说明 |
|------|------|
| `table_html` | 表格（自动识别单条 / 多条记录，支持货币格式化） |
| `topn_single_bar_chart` | TopN 单系列水平条形图（带排名） |
| `topn_multi_bar_chart` | TopN 多系列对比条形图 |
| `multi_line_chart` | 多系列折线图（纯 SVG） |
| `horizontal_single_bar_chart` | 通用水平柱状图（不排序，自定义格式化） |
| `hourly_distribution_bar_chart` | 时段分布柱状图 |
| `daily_trend_bar_chart` | 每日趋势柱状图 |
| `render_metric_section` | 渲染多个指标分组 |
| `format_value` | 按 Field 元信息格式化数值 |
| `create_field` / `create_style` | 模板辅助构造器 |

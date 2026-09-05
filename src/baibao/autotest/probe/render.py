"""
render — DOM 摘要的 markdown 渲染（纯函数，无浏览器依赖）。

把 :data:`baibao.autotest.probe.extract_js.EXTRACT_JS` 提取的结构化 dict 渲染成紧凑 markdown；自定义 JS 的返回值渲染成紧凑 JSON。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["MAX_OUTPUT_CHARS", "format_custom", "format_summary"]

# 摘要硬上限（字符）。超限截断并标注，保证单页输出对 AI 上下文友好。
MAX_OUTPUT_CHARS = 6000


def format_summary(data: dict[str, Any], *, brief: bool = False) -> str:
    """
    把 :data:`EXTRACT_JS` 提取的结构化 dict 渲染成紧凑 markdown。

    空段落自动省略；整体超过 :data:`MAX_OUTPUT_CHARS` 时硬截断并标注。

    Args:
        data: :data:`EXTRACT_JS` 提取的结构化 dict。
        brief: 超简略模式——只留骨架：表单项的 label/类型/必填/静态选项、表格列头与行数、按钮/弹窗/页签等；去掉当前值、首行样本、分页数据（展开中的下拉选项保留，因为点击是有意为之）。
    """
    if brief:
        brief_forms: list[dict[str, Any]] = data.get("forms") or []
        brief_tables: list[dict[str, Any]] = data.get("tables") or []
        data = {
            **data,
            "forms": [
                {k: f.get(k) for k in ("label", "required", "type", "options", "scope")}
                for f in brief_forms
            ],
            "tables": [
                {k: t.get(k) for k in ("cols", "rowCount")}
                for t in brief_tables
            ],
            "pagination": [],
        }
    lines: list[str] = []
    lines.append(f"# 页面摘要：{data.get('title') or '无标题'}")
    lines.append(f"- URL：{data.get('url') or ''}")

    overlays: list[str] = data.get("overlays") or []
    if overlays:
        lines.append(f"\n## 打开中的弹窗/抽屉：{'、'.join(overlays)}")

    forms: list[dict[str, Any]] = data.get("forms") or []
    if forms:
        lines.append(f"\n## 表单项（{len(forms)}）")
        for f in forms:
            req = "[必填] " if f.get("required") else ""
            label = f.get("label") or "无标签"
            parts = [f"- {req}{label}：{f.get('type') or '未知'}"]
            if f.get("value"):
                parts.append(f"当前“{f['value']}”")
            if (
                f.get("placeholder") and not f.get("value")
                and f["placeholder"] != f.get("label")
            ):
                parts.append(f"提示“{f['placeholder']}”")
            if f.get("options"):
                parts.append("选项：" + "、".join(f["options"]))
            if f.get("scope"):
                parts.append(f"@{f['scope']}")
            lines.append(" ".join(parts))

    tables: list[dict[str, Any]] = data.get("tables") or []
    for idx, t in enumerate(tables, 1):
        lines.append(f"\n## 表格{idx}（数据行 {t.get('rowCount', 0)}）")
        cols: list[str] = t.get("cols") or []
        if cols:
            lines.append(f"- 列头：{' | '.join(cols)}")
        first_row: list[str] = t.get("firstRow") or []
        if first_row:
            lines.append(f"- 首行：{' | '.join(first_row)}")

    pagination: list[str] = data.get("pagination") or []
    if pagination:
        lines.append(f"- 分页：{'；'.join(pagination)}")

    tabs: list[str] = data.get("tabs") or []
    if tabs:
        lines.append(f"\n## 页签：{'、'.join(tabs)}")

    buttons: list[str] = data.get("buttons") or []
    if buttons:
        lines.append(f"\n## 按钮：{' / '.join(buttons)}")

    messages: list[str] = data.get("messages") or []
    if messages:
        lines.append(f"\n## 消息：{'；'.join(messages)}")

    errors: list[str] = data.get("errors") or []
    if errors:
        lines.append(f"\n## 校验错误：{'；'.join(errors)}")

    dropdowns: list[dict[str, Any]] = data.get("dropdowns") or []
    for idx, d in enumerate(dropdowns, 1):
        selected: list[str] = d.get("selected") or []
        texts: list[str] = d.get("texts") or []
        empty = d.get("emptyText") or ""
        seg = f"\n## 展开中的下拉{idx}（共 {d.get('count', 0)} 项"
        if selected:
            seg += f"，已选：{'、'.join(selected)}"
        seg += "）"
        lines.append(seg)
        if texts:
            lines.append(f"- 选项：{'、'.join(texts)}")
        if empty:
            lines.append(f"- 状态：{empty}")

    md = "\n".join(lines)
    if not forms and not tables and not buttons and not overlays:
        md += "\n\n（页面无可见 Element Plus 结构：可能是登录页、白屏或路由未命中）"
    if len(md) > MAX_OUTPUT_CHARS:
        md = md[:MAX_OUTPUT_CHARS] + "\n…（超出上限已截断）"
    return md


def format_custom(result: object) -> str:
    """
    把自定义 JS 的返回值渲染成紧凑 JSON（不可序列化时降级 str，超限截断）。
    """
    try:
        text = json.dumps(result, ensure_ascii=False)
    except TypeError:
        text = str(result)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "…（超出上限已截断）"
    return text

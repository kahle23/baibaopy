"""probe 渲染与 URL 规范化的单元测试（format_summary 纯函数：渲染/截断/空页面兜底）。"""

import unittest
from typing import Any

from baibao.autotest.probe import MAX_OUTPUT_CHARS, build_target_url, format_summary
from baibao.autotest.probe.render import format_custom


def _sample_data(**overrides: Any) -> dict[str, Any]:
    """最小可渲染样本（probe 真实返回结构的子集）。"""
    data = {
        "title": "资产管理",
        "url": "http://example.com/#/oa/asset",
        "forms": [
            {
                "label": "资产类型", "required": True, "type": "select",
                "value": "IT设备", "placeholder": "请选择", "options": None,
                "scope": "",
            },
            {
                "label": "备注", "required": False, "type": "文本",
                "value": "", "placeholder": "请输入备注", "options": None,
                "scope": "弹窗「新增」",
            },
            {
                "label": "状态", "required": False, "type": "单选",
                "value": "", "placeholder": "", "options": ["在库", "领用"],
                "scope": "",
            },
        ],
        "tables": [
            {"cols": ["编号", "类型", "操作"], "rowCount": 10,
             "firstRow": ["TH001", "IT设备", "编辑 删除"]},
        ],
        "pagination": ["共 42 条"],
        "buttons": ["新增", "导出"],
        "overlays": ["弹窗「新增」"],
        "messages": ["success: 操作成功"],
        "errors": [],
        "tabs": [],
        "dropdowns": [
            {"count": 3, "emptyText": "", "selected": ["IT设备"],
             "texts": ["IT设备", "办公设备", "家具"]},
        ],
    }
    data.update(overrides)
    return data


class TestFormatSummary(unittest.TestCase):
    """format_summary：markdown 渲染。"""

    def test_basic_sections(self) -> None:
        md = format_summary(_sample_data())
        self.assertIn("# 页面摘要：资产管理", md)
        self.assertIn("- URL：http://example.com/#/oa/asset", md)
        self.assertIn("[必填] 资产类型：select 当前“IT设备”", md)
        self.assertIn("备注：文本 提示“请输入备注” @弹窗「新增」", md)
        self.assertIn("状态：单选 选项：在库、领用", md)
        self.assertIn("- 列头：编号 | 类型 | 操作", md)
        self.assertIn("- 分页：共 42 条", md)
        self.assertIn("## 按钮：新增 / 导出", md)
        self.assertIn("## 消息：success: 操作成功", md)
        self.assertIn("已选：IT设备", md)
        self.assertIn("IT设备、办公设备、家具", md)

    def test_empty_sections_omitted(self) -> None:
        md = format_summary(_sample_data(errors=[], tabs=[], messages=[]))
        self.assertNotIn("## 页签", md)
        self.assertNotIn("## 校验错误", md)

    def test_blank_page_hint(self) -> None:
        data: dict[str, Any] = {
            "title": "登录", "url": "http://example.com/#/login",
            "forms": [], "tables": [], "pagination": [], "buttons": [],
            "overlays": [], "messages": [], "errors": [], "tabs": [],
            "dropdowns": [],
        }
        md = format_summary(data)
        self.assertIn("页面无可见 Element Plus 结构", md)

    def test_brief_mode(self) -> None:
        md = format_summary(_sample_data(), brief=True)
        # 骨架保留
        self.assertIn("[必填] 资产类型：select", md)
        self.assertIn("- 列头：编号 | 类型 | 操作", md)
        self.assertIn("IT设备、办公设备、家具", md)  # 点击展开的下拉选项保留
        # 数据细节去除
        self.assertNotIn("当前“IT设备”", md)   # 当前值
        self.assertNotIn("TH001", md)          # 首行样本
        self.assertNotIn("共 42 条", md)        # 分页数据

    def test_hard_truncation(self) -> None:
        data = _sample_data(
            forms=[
                {"label": f"字段{i}", "required": False, "type": "文本",
                 "value": "x" * 30, "placeholder": "", "options": None,
                 "scope": ""}
                for i in range(400)
            ],
        )
        md = format_summary(data)
        self.assertGreater(len(md), MAX_OUTPUT_CHARS - 100)
        self.assertTrue(md.endswith("…（超出上限已截断）"))


class TestBuildTargetUrl(unittest.TestCase):
    """build_target_url：目标参数规范化。"""

    def test_full_url_passthrough(self) -> None:
        self.assertEqual(
            build_target_url("http://x.com/#/a", "http://y.com"),
            "http://x.com/#/a",
        )

    def test_hash_route(self) -> None:
        self.assertEqual(
            build_target_url("#/it-asset", "http://x.com/"),
            "http://x.com/#/it-asset",
        )

    def test_pure_route_recommended_on_git_bash(self) -> None:
        self.assertEqual(
            build_target_url("it-asset", "http://x.com"),
            "http://x.com/#/it-asset",
        )
        self.assertEqual(
            build_target_url("/it-asset", "http://x.com"),
            "http://x.com/#/it-asset",
        )

    def test_msys_polluted_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            build_target_url("#C:/Program Files/Git/it-asset", "http://x.com")
        with self.assertRaises(RuntimeError):
            build_target_url("C:/Program Files/Git/it-asset", "http://x.com")


class TestFormatCustom(unittest.TestCase):
    """format_custom：自定义 JS 返回值的紧凑 JSON 渲染。"""

    def test_json_serializable(self) -> None:
        self.assertEqual(
            format_custom({"a": [1, "中"]}),
            '{"a": [1, "中"]}',
        )

    def test_not_serializable_fallback_str(self) -> None:
        text = format_custom(object())
        self.assertTrue(text.startswith("<object"))

    def test_truncation(self) -> None:
        text = format_custom("x" * (MAX_OUTPUT_CHARS + 10))
        self.assertTrue(text.endswith("…（超出上限已截断）"))


if __name__ == "__main__":
    unittest.main()

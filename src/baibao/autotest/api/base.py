"""
base — 后端接口基类 ApiBase。

封装 HTTP API 调用的通用骨架：发起请求、防御式 JSON 解析。
所有具体业务接口类继承 :class:`ApiBase`，按端点补充专属方法。

设计要点：

  - 复用浏览器登录态 cookie（通过 Playwright ``APIRequestContext`` 发请求）
  - 防御式 JSON 解析：错误响应可能是纯文本，统一包成 ``{"_rawText": ..., "_status": ...}``
  - 仅提供 ``_post``/``_get``/``_parse_json`` 骨架，业务端点由子类定义

依赖：playwright（仅类型注解，运行时 ``APIRequestContext`` 由调用方传入）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ..core.envutil import normalize_base_url

if TYPE_CHECKING:
    from playwright.sync_api import APIRequestContext, APIResponse

__all__ = ["ApiBase"]


class ApiBase:
    """
    后端接口基类。

    通过 Playwright ``APIRequestContext`` 发请求，复用浏览器登录态 cookie。
    子类按业务端点补充方法（参考 ``_post``/``_get``/``_parse_json`` 用法）。

    Args:
        request: Playwright ``APIRequestContext``，通常来自 ``context.request``。
        base_url: API 基础地址，末尾斜杠会被去除。
        headers: 附加默认请求头（如 token 型项目 ``{"Authorization": token}``），随每个请求发送；cookie 鉴权项目不传即可。
    """

    def __init__(
        self, request: APIRequestContext, base_url: str,
        headers: dict[str, str] | None = None,
    ):
        self.request = request
        self.base_url = normalize_base_url(base_url)
        self.headers: dict[str, str] = dict(headers or {})

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _post(self, path: str, data: dict[str, Any] | None = None) -> APIResponse:
        """
        发起 POST 请求（JSON body）并返回响应对象。
        """
        import json

        resp = self.request.post(
            f"{self.base_url}{path}",
            data=json.dumps(data) if data else "{}",
            headers={"Content-Type": "application/json", **self.headers},
        )
        return resp

    def _get(
        self, path: str, params: dict[str, Any] | None = None,
    ) -> APIResponse:
        """
        发起 GET 请求并返回响应对象。
        """
        return self.request.get(
            f"{self.base_url}{path}", params=params or {}, headers=dict(self.headers),
        )

    def _parse_json(self, resp: APIResponse) -> dict[str, Any]:
        """
        解析响应 JSON。防御式：解析失败时返回原始文本与状态码。
        """
        try:
            return cast("dict[str, Any]", resp.json())
        except Exception:
            return {"_rawText": resp.text(), "_status": resp.status}

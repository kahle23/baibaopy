"""
OpenAI 兼容 LLM 策略实现模块。

支持 OpenAI、DeepSeek、Moonshot 等所有兼容 OpenAI API 格式的服务商，
提供单轮/多轮对话和流式输出功能。
"""

import os
from collections.abc import Generator
from typing import Any

from pykunlun.util import modutil

from ._llm import ChatMessage, ChatResponse, LlmCfg, LlmService


class OpenAiLlm(LlmService):
    """
    基于 OpenAI 兼容 API 的 LLM 策略实现。

    支持 OpenAI 官方 API 以及所有兼容 OpenAI API 格式的服务商，包括：
    - OpenAI（GPT-3.5、GPT-4、GPT-4o 等）
    - DeepSeek
    - Moonshot（月之暗面）
    - 智谱 AI（GLM 系列）
    - 阿里通义千问（DashScope 兼容模式）
    - 本地部署的 OpenAI 兼容服务（如 Ollama、vLLM、LocalAI 等）

    特性:
        - 支持单轮对话、多轮对话和流式输出
        - 支持自定义 API 地址和密钥
        - 支持环境变量配置（OPENAI_API_KEY、OPENAI_BASE_URL）
        - 自动安装 openai 依赖

    依赖:
        - openai: OpenAI 官方 Python SDK

    示例::

        from baibao.ai.llm import LlmCfg
        from baibao.ai.llm.openai_llm import OpenAiLlm

        # OpenAI 官方
        cfg = LlmCfg(service_type="openai", api_key="sk-xxx")
        llm = OpenAiLlm(cfg)

        # DeepSeek
        cfg = LlmCfg(
            service_type="openai",
            api_key="sk-xxx",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        )
        llm = OpenAiLlm(cfg)

        # 本地 Ollama
        cfg = LlmCfg(
            service_type="openai",
            api_key="ollama",
            base_url="http://localhost:11434/v1",
            model="llama3",
        )
        llm = OpenAiLlm(cfg)
    """

    def __init__(
        self,
        cfg: LlmCfg,
    ) -> None:
        """
        初始化 OpenAI 兼容 LLM 策略。

        Args:
            cfg: LLM 配置对象，包含 api_key、base_url、model、timeout、max_retries 等配置。

        Raises:
            ImportError: 当 openai 库未安装且自动安装失败时抛出。
            ValueError: 当 api_key 未提供且环境变量中也未找到时抛出。
        """
        # openai 为可选依赖：未安装时 modutil 自动 pip 安装后重导入
        openai = modutil.import_module('openai')

        # 解析 API Key
        resolved_api_key = (
            cfg.api_key
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
        )
        if not resolved_api_key:
            raise ValueError(
                "未提供 api_key，且未找到环境变量 OPENAI_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY"
            )

        # 解析 Base URL
        resolved_base_url = (
            cfg.base_url
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
        )

        if not cfg.model:
            raise ValueError("未提供 model 配置，请在配置中指定模型名称")
        
        self._model: str = cfg.model
        self._client = openai.OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout=cfg.timeout,
            max_retries=cfg.max_retries,
        )

    @property
    def model(self) -> str:
        """
        获取当前配置的模型名称。

        Returns:
            str: 模型名称，如 'gpt-4o-mini'。
        """
        return self._model

    @property
    def client(self) -> Any:
        """
        获取底层的 OpenAI 客户端实例，供高级用户使用。

        Returns:
            openai.OpenAI: OpenAI 客户端实例。
        """
        return self._client

    def _build_messages(
        self,
        prompt: str,
        system: str | None = None,
    ) -> list[dict[str, str]]:
        """
        构建消息列表。

        Args:
            prompt: 用户提示文本。
            system: 系统提示文本。

        Returns:
            符合 OpenAI API 格式的消息列表。
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _to_api_messages(
        self,
        messages: list[ChatMessage],
    ) -> list[dict[str, str]]:
        """
        将 ChatMessage 列表转换为 OpenAI API 格式。

        Args:
            messages: ChatMessage 对象列表。

        Returns:
            符合 OpenAI API 格式的消息列表。
        """
        return [{"role": m.role, "content": m.content} for m in messages]

    def _build_response(self, raw_response: Any) -> ChatResponse:
        """
        将 OpenAI 原始响应转换为 ChatResponse。

        Args:
            raw_response: OpenAI API 返回的 ChatCompletion 对象。

        Returns:
            :class:`ChatResponse` 对象。
        """
        choice = raw_response.choices[0]
        usage = {}
        if raw_response.usage:
            usage = {
                "prompt_tokens": raw_response.usage.prompt_tokens,
                "completion_tokens": raw_response.usage.completion_tokens,
                "total_tokens": raw_response.usage.total_tokens,
            }
        return ChatResponse(
            content=choice.message.content or "",
            model=raw_response.model,
            usage=usage,
            finish_reason=choice.finish_reason or "",
            raw=raw_response,
        )

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """
        对话，传入完整消息历史。

        Args:
            messages: 消息列表，包含完整的对话历史。
            temperature: 温度参数，控制输出的随机性，默认 0.7。
            max_tokens: 最大生成 token 数量，None 表示不限制。
            **kwargs: 其他参数，如 top_p、frequency_penalty、presence_penalty 等。

        Returns:
            :class:`ChatResponse` 对象，包含响应文本和元数据。

        Raises:
            openai.APIConnectionError: 网络连接失败。
            openai.AuthenticationError: API 密钥无效。
            openai.RateLimitError: 请求频率超限。
            openai.APIError: 其他 API 错误。
        """
        api_messages = self._to_api_messages(messages)
        params: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens

        raw_response = self._client.chat.completions.create(**params)
        return self._build_response(raw_response)

    def stream_chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Generator[str, None, None]:
        """
        流式对话，逐步返回生成的文本片段。

        Args:
            messages: 消息列表，包含完整的对话历史。
            temperature: 温度参数，控制输出的随机性，默认 0.7。
            max_tokens: 最大生成 token 数量，None 表示不限制。
            **kwargs: 其他参数，如 top_p、frequency_penalty、presence_penalty 等。

        Yields:
            str: 生成的文本片段，拼接后为完整响应。

        Raises:
            openai.APIConnectionError: 网络连接失败。
            openai.AuthenticationError: API 密钥无效。
            openai.RateLimitError: 请求频率超限。
            openai.APIError: 其他 API 错误。
        """
        api_messages = self._to_api_messages(messages)
        params: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens

        stream = self._client.chat.completions.create(**params)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

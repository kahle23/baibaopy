"""
LLM 核心模块，定义接口、数据对象和管理函数。

包含配置类（LlmCfg）、消息类（ChatMessage/ChatResponse）、
策略抽象基类（LlmService）及模块级服务管理函数。
"""

from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any


# region ======== 配置类 ========
@dataclass
class LlmCfg:
    """
    LLM 配置类。

    用于从 JSON 配置文件加载 LLM 服务配置。

    Attributes:
        service_type: LLM 服务类型，如 'openai'。
        api_key: API 密钥。
        base_url: API 基础地址。
        model: 模型名称，默认 None。
        timeout: 请求超时时间（秒），默认 60.0。
        max_retries: 最大重试次数，默认 2。
    """

    service_type: str
    api_key: str
    base_url: str | None = None
    model: str | None = None
    timeout: float = 60.0
    max_retries: int = 2

    @staticmethod
    def load_from_json_cfg(config_path: str | Path) -> 'LlmCfg':
        """
        从 JSON 文件加载 LLM 配置。

        Args:
            config_path: JSON 配置文件路径，支持字符串或 Path 对象。

        Returns:
            LlmCfg 实例对象。

        Raises:
            FileNotFoundError: 文件不存在时抛出。
            ValueError: 文件缺少必填字段时抛出。
            json.JSONDecodeError: JSON 格式解析失败时抛出。
        """
        from pykunlun.util import loadutil
        cfg = loadutil.load_dataclass_from_json_file(config_path, LlmCfg)
        return cfg
# endregion


# region ======== 数据对象 ========
@dataclass
class ChatMessage:
    """
    对话消息。

    Attributes:
        role: 消息角色，可选值为 'system'、'user'、'assistant'、'tool'、'function'。
        content: 消息内容。
    """

    role: str
    content: str


@dataclass
class ChatResponse:
    """
    LLM 响应结果。

    Attributes:
        content: 响应文本内容。
        model: 使用的模型名称。
        usage: token 使用情况，包含 prompt_tokens、completion_tokens、total_tokens。
        finish_reason: 结束原因，如 'stop'、'length' 等。
        raw: 原始响应对象，供高级用户使用。
    """

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict[str, int])
    finish_reason: str = ""
    raw: Any = None
# endregion


# region ======== 策略抽象基类 ========
class LlmService(ABC):
    """
    LLM 策略抽象基类，定义统一的对话接口。

    所有具体实现（如 OpenAiLlm）需继承此类并实现核心方法，
    以保证接口的一致性和可替换性。
    """

    @abstractmethod
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
            **kwargs: 其他模型特定参数。

        Returns:
            :class:`ChatResponse` 对象，包含响应文本和元数据。

        Raises:
            ValueError: 参数不合法。
            ConnectionError: 网络连接失败。
            RuntimeError: API 调用失败。
        """
        pass

    @abstractmethod
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
            **kwargs: 其他模型特定参数。

        Yields:
            str: 生成的文本片段，拼接后为完整响应。

        Raises:
            ValueError: 参数不合法。
            ConnectionError: 网络连接失败。
            RuntimeError: API 调用失败。
        """
        pass
# endregion


# region ======== 模块级 LLM 管理 ========
# 存储不同配置名对应的 LlmService 实例
_llmServices: dict[str, LlmService] = {}
# 保护 _llmServices 字典并发访问的锁
_llmServices_lock = Lock()
# 默认配置名
DEFAULT_LLM_NAME = "default"


def get_llm_service(llm_name: str | None = None) -> LlmService:
    """
    获取指定配置名对应的 LlmService 实例。

    对于默认配置名，如果尚未设置，会自动从 ./llm.config 文件加载配置并初始化服务。

    Args:
        llm_name: LLM 配置名，如果不传则使用默认配置名。

    Returns:
        LlmService 实例。

    Raises:
        KeyError: 指定的配置名对应的 LlmService 不存在时抛出。
    """
    # 如果未指定配置名，使用默认配置名
    if not llm_name:
        llm_name = DEFAULT_LLM_NAME
    # 使用锁保护全局字典的访问
    with _llmServices_lock:
        # 如果配置名不存在，并且是默认配置名，尝试从 ./llm.config 加载配置
        if llm_name not in _llmServices:
            if llm_name == DEFAULT_LLM_NAME:
                try:
                    cfg = LlmCfg.load_from_json_cfg("./llm.config")
                    # 根据 service_type 创建对应的 LlmService 实例
                    if cfg.service_type == 'openai':
                        from .openai_llm import OpenAiLlm
                        _llmServices[llm_name] = OpenAiLlm(cfg)
                    else:
                        raise ValueError(f"不支持的 LLM 服务类型: {cfg.service_type}")
                except FileNotFoundError as e:
                    raise KeyError("自动加载默认配置失败: 找不到配置文件 './llm.config'") from e
                except ValueError as e:
                    raise KeyError(f"自动加载默认配置失败: 配置格式错误 - {e}") from e
                except Exception as e:
                    raise KeyError(f"自动加载默认配置失败: {e}") from e
            else:
                raise KeyError(f"未找到配置名 '{llm_name}' 对应的 LlmService，请先调用 set_llm_service() 设置")
        # 返回对应的 LlmService 实例
        return _llmServices[llm_name]


def set_llm_service(llm_name: str, service: LlmService) -> None:
    """
    设置指定配置名对应的 LlmService 实例。

    Args:
        llm_name: LLM 配置名。
        service: LlmService 实例。

    Raises:
        TypeError: service 不是 LlmService 类型时抛出。
    """
    if not isinstance(service, LlmService):
        raise TypeError(
            f"service 必须是 LlmService 类型，实际类型: {type(service)}"
        )
    if not llm_name:
        llm_name = DEFAULT_LLM_NAME
    # 使用锁保护全局字典的访问
    with _llmServices_lock:
        _llmServices[llm_name] = service


def remove_llm_service(llm_name: str | None = None) -> None:
    """
    移除指定配置名对应的 LlmService 实例。

    Args:
        llm_name: LLM 配置名，如果不传则移除默认配置名。
    """
    if not llm_name:
        llm_name = DEFAULT_LLM_NAME
    # 使用锁保护全局字典的访问
    with _llmServices_lock:
        _llmServices.pop(llm_name, None)


def chat(
    messages: list[ChatMessage],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    llm_name: str | None = None,
    **kwargs: Any,
) -> ChatResponse:
    """
    对话，传入完整消息历史。

    Args:
        messages: 消息列表，包含完整的对话历史。
        temperature: 温度参数，控制输出的随机性，默认 0.7。
        max_tokens: 最大生成 token 数量，None 表示不限制。
        llm_name: LLM 配置名，如果不传则使用默认配置名。
        **kwargs: 其他模型特定参数。

    Returns:
        :class:`ChatResponse` 对象，包含响应文本和元数据。
    """
    return get_llm_service(llm_name).chat(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


def stream_chat(
    messages: list[ChatMessage],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    llm_name: str | None = None,
    **kwargs: Any,
) -> Generator[str, None, None]:
    """
    流式对话，逐步返回生成的文本片段。

    Args:
        messages: 消息列表，包含完整的对话历史。
        temperature: 温度参数，控制输出的随机性，默认 0.7。
        max_tokens: 最大生成 token 数量，None 表示不限制。
        llm_name: LLM 配置名，如果不传则使用默认配置名。
        **kwargs: 其他模型特定参数。

    Yields:
        str: 生成的文本片段，拼接后为完整响应。
    """
    yield from get_llm_service(llm_name).stream_chat(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
# endregion

"""
模板引擎管理模块，统一管理并支持运行时切换模板引擎实现。

定义 TemplateEngine 抽象基类和模块级管理函数，采用流式渲染架构，
支持文件、字符串、流等多种输入输出形式的模板渲染操作。
"""

import threading
from abc import ABC, abstractmethod
from io import StringIO
from typing import IO, Any


class TemplateEngine(ABC):
    """
    模板引擎抽象基类，定义统一的渲染接口。

    所有具体实现（如 Jinja2Engine）需继承此类并实现 :meth:`render_stream_to_stream` 核心方法，
    其余方法均基于该核心方法提供默认实现，以保证接口的一致性和可替换性。

    方法总览：

    - :meth:`render_stream_to_stream` —— **唯一抽象方法**，输入流 → 输出流（核心）
    - :meth:`render_file_to_file` —— 模板文件 → 输出文件（基于核心方法的默认实现）
    - :meth:`render_string_to_file` —— 模板字符串 → 输出文件（基于核心方法的默认实现）
    - :meth:`render_string_to_string` —— 模板字符串 → 返回字符串（基于核心方法的默认实现）
    - :meth:`render_file_to_string` —— 模板文件 → 返回字符串（基于核心方法的默认实现）
    """

    @abstractmethod
    def render_stream_to_stream(self, input_stream: IO[str], output_stream: IO[str], **kwargs: Any) -> None:
        """
        从输入流读取模板内容，渲染后写入输出流。**子类必须实现此方法。**

        这是模板引擎的核心方法，其余四个方法均基于此方法提供默认实现。
        适用于大文件渲染场景，模板内容不会完整加载到内存。

        Args:
            input_stream: 可读的文本输入流，包含模板内容。
            output_stream: 可写的文本输出流，用于接收渲染结果。
            **kwargs: 模板变量，以关键字参数形式传入。

        Raises:
            ValueError: 模板语法错误或变量缺失。
            OSError: 流读写失败。
        """
        pass

    def render_file_to_file(self, template_path: str, output_path: str,
                            encoding: str | None = None, **kwargs: Any) -> None:
        """
        渲染模板文件，将结果直接写入另一个文件。

        默认实现基于 :meth:`render_stream_to_stream`，子类可覆盖以优化性能。

        Args:
            template_path: 模板文件路径。
            output_path: 输出文件路径。
            encoding: 文件编码，如果为 None 则默认使用 'utf-8'。
            **kwargs: 模板变量，以关键字参数形式传入。

        Raises:
            FileNotFoundError: 模板文件不存在。
            ValueError: 模板语法错误或变量缺失。
            OSError: 文件读写失败。
        """
        encoding = encoding or 'utf-8'
        with open(template_path, 'r', encoding=encoding) as fin, \
             open(output_path, 'w', encoding=encoding) as fout:
            self.render_stream_to_stream(fin, fout, **kwargs)

    def render_string_to_file(self, template_string: str, output_path: str,
                              encoding: str | None = None, **kwargs: Any) -> None:
        """
        渲染模板字符串，将结果直接写入文件。

        默认实现基于 :meth:`render_stream_to_stream`，子类可覆盖以优化性能。

        Args:
            template_string: 模板字符串内容。
            output_path: 输出文件路径。
            encoding: 输出文件编码，如果为 None 则默认使用 'utf-8'。
            **kwargs: 模板变量，以关键字参数形式传入。

        Raises:
            ValueError: 模板语法错误或变量缺失。
            OSError: 文件写入失败。
        """
        encoding = encoding or 'utf-8'
        with StringIO(template_string) as sin, \
             open(output_path, 'w', encoding=encoding) as fout:
            self.render_stream_to_stream(sin, fout, **kwargs)

    def render_file_to_string(self, template_path: str,
                              encoding: str | None = None, **kwargs: Any) -> str:
        """
        渲染模板文件，返回渲染后的文本结果。

        默认实现基于 :meth:`render_stream_to_stream`，子类可覆盖以优化性能。

        Args:
            template_path: 模板文件路径。
            encoding: 模板文件编码，如果为 None 则默认使用 'utf-8'。
            **kwargs: 模板变量，以关键字参数形式传入。

        Returns:
            渲染后的文本内容。

        Raises:
            FileNotFoundError: 模板文件不存在。
            ValueError: 模板语法错误或变量缺失。
        """
        encoding = encoding or 'utf-8'
        with open(template_path, 'r', encoding=encoding) as fin, \
             StringIO() as sout:
            self.render_stream_to_stream(fin, sout, **kwargs)
            return sout.getvalue()

    def render_string_to_string(self, template_string: str, **kwargs: Any) -> str:
        """
        渲染模板字符串，返回渲染后的文本结果。

        默认实现基于 :meth:`render_stream_to_stream`，子类可覆盖以优化性能。

        Args:
            template_string: 模板字符串内容。
            **kwargs: 模板变量，以关键字参数形式传入。

        Returns:
            渲染后的文本内容。

        Raises:
            ValueError: 模板语法错误或变量缺失。
        """
        with StringIO(template_string) as sin, \
             StringIO() as sout:
            self.render_stream_to_stream(sin, sout, **kwargs)
            return sout.getvalue()


# 子类导入必须在 TemplateEngine 定义之后，避免循环导入
# "as Jinja2Engine" 是 mypy 显式再导出惯用法（strict 禁止隐式转发），
# 与 ruff PLC0414 冲突，此处以 mypy 为准
from .jinja2_engine import Jinja2Engine as Jinja2Engine  # noqa: PLC0414

# 存储不同配置名对应的 TemplateEngine 实例
_template_engines: dict[str, TemplateEngine] = {}
# 可重入锁，保护 _template_engines 的并发访问
_lock = threading.RLock()
# 默认配置名
DEFAULT_ENGINE_NAME = "default"


def get_template_engine(engine_name: str | None = None) -> TemplateEngine:
    """
    获取指定配置名对应的 TemplateEngine 实例。

    对于默认配置名，如果尚未设置，会自动创建 Jinja2Engine 实例。

    Args:
        engine_name: 模板引擎配置名，如果不传则使用默认配置名

    Returns:
        TemplateEngine 实例

    Raises:
        KeyError: 指定的配置名对应的 TemplateEngine 不存在时抛出
    """
    with _lock:
        # 如果未指定配置名，使用默认配置名
        if not engine_name:
            engine_name = DEFAULT_ENGINE_NAME
        # 如果配置名不存在，并且是默认配置名，设置为 Jinja2Engine 实例
        if engine_name not in _template_engines:
            if engine_name == DEFAULT_ENGINE_NAME:
                _template_engines[engine_name] = Jinja2Engine()
            else:
                raise KeyError(f"未找到配置名 '{engine_name}' 对应的 TemplateEngine，请先调用 set_template_engine() 设置")
        # 返回对应的 TemplateEngine 实例
        return _template_engines[engine_name]


def set_template_engine(engine_name: str, engine: TemplateEngine) -> None:
    """
    设置指定配置名对应的 TemplateEngine 实例。

    Args:
        engine_name: 模板引擎配置名
        engine: TemplateEngine 实例
    """
    with _lock:
        # 检查 engine 是否为 TemplateEngine 类型
        if not isinstance(engine, TemplateEngine):
            raise TypeError(f"engine 必须是 TemplateEngine 类型，实际类型: {type(engine)}")
        # 如果未指定配置名，使用默认配置名
        if not engine_name:
            engine_name = DEFAULT_ENGINE_NAME
        # 设置对应的 TemplateEngine 实例
        _template_engines[engine_name] = engine


def remove_template_engine(engine_name: str | None = None) -> None:
    """
    移除指定配置名对应的 TemplateEngine 实例。

    Args:
        engine_name: 模板引擎配置名，如果不传则移除默认配置名
    """
    with _lock:
        # 如果未指定配置名，使用默认配置名
        if not engine_name:
            engine_name = DEFAULT_ENGINE_NAME
        # 如果配置名存在，移除对应的 TemplateEngine 实例
        _template_engines.pop(engine_name, None)


def render_stream_to_stream(input_stream: IO[str], output_stream: IO[str],
                            engine_name: str | None = None, **kwargs: Any) -> None:
    """
    从输入流读取模板内容，渲染后写入输出流。

    Args:
        input_stream: 可读的文本输入流，包含模板内容。
        output_stream: 可写的文本输出流，用于接收渲染结果。
        engine_name: 模板引擎配置名，如果不传则使用默认配置名。
        **kwargs: 模板变量，以关键字参数形式传入。
    """
    get_template_engine(engine_name).render_stream_to_stream(input_stream, output_stream, **kwargs)


def render_file_to_file(template_path: str, output_path: str,
                        encoding: str | None = None,
                        engine_name: str | None = None, **kwargs: Any) -> None:
    """
    渲染模板文件，将结果直接写入另一个文件。

    Args:
        template_path: 模板文件路径。
        output_path: 输出文件路径。
        encoding: 文件编码，如果为 None 则默认使用 'utf-8'。
        engine_name: 模板引擎配置名，如果不传则使用默认配置名。
        **kwargs: 模板变量，以关键字参数形式传入。

    Raises:
        FileNotFoundError: 模板文件不存在。
    """
    get_template_engine(engine_name).render_file_to_file(
        template_path, output_path, encoding=encoding, **kwargs)


def render_string_to_file(template_string: str, output_path: str,
                          encoding: str | None = None,
                          engine_name: str | None = None, **kwargs: Any) -> None:
    """
    渲染模板字符串，将结果直接写入文件。

    Args:
        template_string: 模板字符串内容。
        output_path: 输出文件路径。
        encoding: 输出文件编码，如果为 None 则默认使用 'utf-8'。
        engine_name: 模板引擎配置名，如果不传则使用默认配置名。
        **kwargs: 模板变量，以关键字参数形式传入。
    """
    get_template_engine(engine_name).render_string_to_file(
        template_string, output_path, encoding=encoding, **kwargs)


def render_file_to_string(template_path: str,
                          encoding: str | None = None,
                          engine_name: str | None = None, **kwargs: Any) -> str:
    """
    渲染模板文件，返回渲染后的文本结果。

    Args:
        template_path: 模板文件路径。
        encoding: 模板文件编码，如果为 None 则默认使用 'utf-8'。
        engine_name: 模板引擎配置名，如果不传则使用默认配置名。
        **kwargs: 模板变量，以关键字参数形式传入。

    Returns:
        渲染后的文本内容。

    Raises:
        FileNotFoundError: 模板文件不存在。
    """
    return get_template_engine(engine_name).render_file_to_string(
        template_path, encoding=encoding, **kwargs)


def render_string_to_string(template_string: str,
                            engine_name: str | None = None, **kwargs: Any) -> str:
    """
    渲染模板字符串，返回渲染后的文本结果。

    Args:
        template_string: 模板字符串内容。
        engine_name: 模板引擎配置名，如果不传则使用默认配置名。
        **kwargs: 模板变量，以关键字参数形式传入。

    Returns:
        渲染后的文本内容。
    """
    return get_template_engine(engine_name).render_string_to_string(template_string, **kwargs)

"""
Jinja2 模板引擎策略实现模块。

基于 Jinja2 库实现 TemplateEngine 抽象接口，支持模板继承、自定义过滤器、
自动转义等特性，适用于 HTML 渲染和代码生成等场景。
"""
import os
from collections.abc import Callable
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    import jinja2

from pykunlun.util import modutil

from ._template import TemplateEngine


class Jinja2Engine(TemplateEngine):
    """
    基于 Jinja2 库的模板引擎策略实现。

    Jinja2 是一个现代的、设计优雅的 Python 模板引擎，广泛应用于 Web 开发（如 Flask 框架）。
    本实现仅需实现 :meth:`render_stream_to_stream` 核心方法，其余方法继承自基类默认实现。

    特性:
        - 支持流式渲染，适用于大文件场景
        - 支持模板继承和包含
        - 支持自定义过滤器和全局函数
        - 支持自动转义（可配置）
        - 支持模板缓存（可配置）

    依赖:
        - jinja2: 核心模板引擎
    """

    def __init__(
        self,
        template_dir: str | None = None,
        auto_escape: bool = True,
        cache_size: int = 400,
        undefined: str = "strict",
        filters: dict[str, Callable[..., Any]] | None = None,
        globals: dict[str, Any] | None = None,
    ) -> None:
        """
        初始化 Jinja2 模板引擎。

        Args:
            template_dir: 模板文件目录，用于加载文件模板。如果为 None，则只能渲染模板字符串。
            auto_escape: 是否启用自动转义，默认为 True。启用后会自动转义 HTML 特殊字符，
                        防止 XSS 攻击。对于非 HTML 模板，建议设置为 False。
            cache_size: 模板缓存大小，默认为 400。设置为 0 禁用缓存。
            undefined: 未定义变量的处理方式，可选值：
                      - "strict": 抛出 UndefinedError（默认）
                      - "undefined": 返回空字符串
                      - "debug": 返回调试信息
            filters: 自定义过滤器字典，键为过滤器名称，值为过滤器函数。
            globals: 全局变量字典，可在所有模板中访问。

        Raises:
            ImportError: 当 jinja2 库未安装且自动安装失败时抛出。
        """
        # 导入 Jinja2 库
        modutil.import_module('jinja2')
        # 初始化 Jinja2 环境配置
        self._template_dir = template_dir
        self._auto_escape = auto_escape
        self._cache_size = cache_size
        self._undefined = undefined
        self._filters = filters or {}
        self._globals = globals or {}
        # 创建 Jinja2 环境
        self._env = self._create_environment()

    def _create_environment(self) -> 'jinja2.Environment':
        """
        创建 Jinja2 环境。

        Returns:
            配置好的 Jinja2 Environment 实例。
        """
        # 导入 Jinja2 库
        import jinja2
        # 配置未定义变量处理
        undefined: type[jinja2.Undefined]
        if self._undefined == "strict":
            undefined = jinja2.StrictUndefined
        elif self._undefined == "undefined":
            undefined = jinja2.Undefined
        elif self._undefined == "debug":
            undefined = jinja2.DebugUndefined
        else:
            raise ValueError(f"不支持的 undefined 值: {self._undefined}，可选值: strict, undefined, debug")
        # 创建加载器
        loader: jinja2.BaseLoader
        if self._template_dir:
            if not os.path.exists(self._template_dir):
                raise FileNotFoundError(f"模板目录不存在: {self._template_dir}")
            loader = jinja2.FileSystemLoader(self._template_dir)
        else:
            loader = jinja2.BaseLoader()
        # 创建环境
        env = jinja2.Environment(
            loader=loader,
            autoescape=self._auto_escape,
            cache_size=self._cache_size,
            undefined=undefined,
        )
        # 添加自定义过滤器
        env.filters.update(self._filters)
        # 添加全局变量
        env.globals.update(self._globals)
        # 返回配置好的 Jinja2 环境
        return env

    @property
    def template_dir(self) -> str | None:
        """
        获取模板目录路径。

        Returns:
            模板目录路径，如果未设置则返回 None。
        """
        return self._template_dir

    @property
    def auto_escape(self) -> bool:
        """
        获取是否启用自动转义。

        Returns:
            True 表示启用自动转义，False 表示禁用。
        """
        return self._auto_escape

    @property
    def cache_size(self) -> int:
        """
        获取模板缓存大小。

        Returns:
            缓存大小，0 表示禁用缓存。
        """
        return self._cache_size

    @property
    def undefined(self) -> str:
        """
        获取未定义变量的处理方式。

        Returns:
            处理方式字符串，可选值: strict, undefined, debug。
        """
        return self._undefined

    @property
    def filters(self) -> dict[str, Callable[..., Any]]:
        """
        获取所有自定义过滤器。

        Returns:
            过滤器字典，键为过滤器名称，值为过滤器函数。
        """
        return self._filters.copy()

    @property
    def globals(self) -> dict[str, Any]:
        """
        获取所有全局变量。

        Returns:
            全局变量字典，键为变量名称，值为变量值。
        """
        return self._globals.copy()

    def add_filter(self, name: str, filter_func: Callable[..., Any]) -> None:
        """
        添加自定义过滤器。

        Args:
            name: 过滤器名称。
            filter_func: 过滤器函数。
        """
        # 添加过滤器到 Jinja2 环境
        self._env.filters[name] = filter_func
        # 更新过滤器字典
        self._filters[name] = filter_func

    def add_global(self, name: str, value: Any) -> None:
        """
        添加全局变量。

        Args:
            name: 变量名称。
            value: 变量值。
        """
        # 添加全局变量到 Jinja2 环境
        self._env.globals[name] = value
        # 更新全局变量字典
        self._globals[name] = value

    def render_stream_to_stream(self, input_stream: IO[str], output_stream: IO[str], **kwargs: Any) -> None:
        """
        从输入流读取模板内容，渲染后写入输出流。

        注意：Jinja2 模板解析必须读取完整内容才能解析语法（变量 ``{{ }}``、控制结构 ``{% %}`` 等），
        因此输入流会被完整读取。但渲染输出通过 ``Template.generate()`` 逐块生成写入，
        避免渲染结果完整驻留内存（适用于循环展开等产生大输出的场景）。

        Args:
            input_stream: 可读的文本输入流，包含模板内容。
            output_stream: 可写的文本输出流，用于接收渲染结果。
            **kwargs: 模板变量，以关键字参数形式传入。

        Raises:
            ValueError: 模板语法错误或变量缺失。
            OSError: 流读写失败。
        """
        template_string = input_stream.read()
        try:
            template = self._env.from_string(template_string)
        except Exception as e:
            raise ValueError(f"模板语法错误: {e}")
        for chunk in template.generate(**kwargs):
            output_stream.write(chunk)
        output_stream.flush()


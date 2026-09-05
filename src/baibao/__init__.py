"""
百宝 — 方便好用的 Python 常用功能库。

把日常开发中反复用到的能力封装成简洁的 API，开箱即用。
涵盖日志、包管理、数据库、消息发送、文字识别等常用场景。

按子模块组织，请按需从子包导入，例如::

    from baibao.db import rdb, RdbCfg
    from baibao.ai.ocr import recognize
    from baibao.render import html, template

顶层包不再 re-export 子模块符号，以避免 import baibao 时被迫加载
全部重依赖（OCR / LLM / 邮件 / 数据库等）。
"""

from pykunlun.envinfo import pkginfo

# 不捕获 PackageNotFoundError：能执行到此处说明包已加载，版本缺失应报错而非静默回退
__version__ = pkginfo.get_package_version(__name__)

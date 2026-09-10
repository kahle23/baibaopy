"""
对象存储模块。

基于 pykunlun 的 :class:`~pykunlun.oss.OssClient` / :class:`~pykunlun.oss.OssManager`
门面抽象，提供阿里云 OSS 实现，以及模块级默认管理器实例 :data:`oss_mgr`：
用户直接拿它按名称注册 client、经便捷方法操作对象存储。

本地目录实现复用 pykunlun 内置的 :class:`~pykunlun.oss.LocalOssClient`（零依赖，
本地开发/单测默认）；阿里云实现 :class:`AliyunOssClient` 基于 oss2 SDK，
未安装时首次使用自动 ``pip install oss2``。

实例统一收敛在 :data:`oss_mgr`（:class:`~pykunlun.oss.OssManager`）：按 ``name``
别名管理多个不同配置的实例，所有操作均可经管理器按名称转发。
首次按名称取实例时触发配置加载：注册内置实现类，并从 ``.baibao/oss.config``
（当前目录或用户目录）读取各实例配置，格式为 ``{实例名: OssCfg 字段映射}``::

    {
      "default":     {"oss_type": "local", "bucket": "demo",
                       "storage_options": {"base_dir": "D:/data/oss"}},
      "aliyun-prod": {"oss_type": "aliyun", "bucket": "my-bucket",
                       "region": "cn-hangzhou", "prefix": "reports/",
                       "access_key_id": "AK", "access_key_secret": "SK"}
    }

典型用法::

    from baibao.oss import oss_mgr, AliyunOssClient, OssCfg

    # 经配置文件（.baibao/oss.config）或代码注册实例
    oss_mgr.register("prod", AliyunOssClient(OssCfg(
        oss_type='aliyun', bucket='my-bucket', region='cn-hangzhou',
        access_key_id='AK', access_key_secret='SK')))

    # 按别名操作（省略 name 时用默认名 "default"）
    oss_mgr.put_object('reports/2026.txt', 'hello', name="prod")
    text = oss_mgr.get_object_text('reports/2026.txt', name="prod")
    url  = oss_mgr.presigned_url('reports/2026.txt', expires=600, name="prod")
"""

import json
from typing import Any

from pykunlun.oss import LocalOssClient, ObjectStat, OssCfg, OssClient, OssManager
from pykunlun.util import ResolveType, fileutil, logutil

from .aliyun_client import AliyunOssClient

log = logutil.getLogger(__name__)

#: 配置加载幂等标记：只加载一次，失败也不重试（避免每次 get_client 都刷告警）
_config_loaded = False


def _config_loader(manager: OssManager, name: str) -> None:
    """
    配置加载器：注册实现类并从 ``.baibao/oss.config`` 加载各实例配置，只执行一次。

    Args:
        manager: 发起加载的管理器（实现类与实例均注册到它身上）。
        name: 触发加载的实例名（本加载器为全量加载，不按 name 区分）。
    """
    global _config_loaded
    if _config_loaded:
        return
    _config_loaded = True

    # 注册实现类（local 来自 pykunlun 内置，aliyun 来自本包）
    manager.register_client_class(LocalOssClient)
    manager.register_client_class(AliyunOssClient)

    # 从配置文件加载实例（{实例名: OssCfg 字段映射}）
    # 捕获具体异常降级告警：配置缺文件/格式坏/字段错均不阻断后续手动 register
    try:
        content = fileutil.read_text(".baibao/oss.config",
                                     search_dirs=[ResolveType.CURRENT, ResolveType.USER])
        config: dict[str, dict[str, Any]] = json.loads(content)
        for n, cfg_data in config.items():
            manager.register(n, OssCfg(**cfg_data))
    except (OSError, ValueError, TypeError) as e:
        log.warning("Failed to load oss config: %s", e)


#: 模块级默认管理器实例：按名称（别名）管理各对象存储客户端实例，
#: 首次按名称取实例时自动加载 .baibao/oss.config。
oss_mgr: OssManager = OssManager(config_loader=_config_loader)

__all__ = [
    'AliyunOssClient',
    'LocalOssClient',
    'ObjectStat',
    'OssCfg',
    'OssClient',
    'OssManager',
    'oss_mgr',
]

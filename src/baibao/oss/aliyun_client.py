"""
阿里云 OSS 客户端（基于官方 SDK ``oss2``，按需自动安装）。

继承 :class:`~pykunlun.oss.OssClient`，把阿里云 OSS 落到统一门面上：
底层钩子直接映射 oss2 的对象接口（put_object/get_object/delete_object/
head_object/copy_object + ObjectIteratorV2 列举），大文件走
``oss2.resumable_upload`` / ``oss2.resumable_download`` 分片断点续传。

SDK 模块由本类经 :func:`pykunlun.util.modutil.import_module` 自行导入与按需安装
（``pip install oss2``），未安装时首次使用才触发安装，构造客户端零副作用。

连接为惰性建立：构造时仅校验配置，首次操作时按桶名构建 ``oss2.Bucket`` 并缓存
（同一桶名复用同一句柄），跨桶复制/移动由 oss2 的 copy_object 原生支持；
:meth:`AliyunOssClient.close` 清空缓存句柄，连接池随句柄对象交由垃圾回收。
"""

import threading
from datetime import datetime
from typing import Any, BinaryIO, ClassVar

from pykunlun.oss import ObjectStat, OssCfg, OssClient
from pykunlun.util import fileutil, logutil, modutil, validation

log = logutil.getLogger(__name__)


class AliyunOssClient(OssClient):
    """
    阿里云 OSS 客户端（oss2 SDK）。

    配置要求（:class:`~pykunlun.oss.OssCfg`，构造时校验）：

      - ``bucket`` / ``access_key_id`` / ``access_key_secret`` 必填；
      - ``endpoint`` 与 ``region`` 至少提供一个（同时提供时 endpoint 优先；
        仅提供 region 时推导为 ``https://oss-{region}.aliyuncs.com``）；
      - ``security_token`` 可选，提供时改用 STS 临时凭证鉴权；
      - ``prefix`` 可选，语义同基类（虚拟目录隔离，对调用方透明）。

    桶寻址：各底层钩子首参的 ``bucket`` 直接作为 oss2 API 的桶参数，
    鉴权身份仍来自配置（一个客户端实例 = 一套凭证），跨桶操作原生支持。

    用法::

        from baibao.oss import AliyunOssClient, OssCfg

        client = AliyunOssClient(OssCfg(
            oss_type='aliyun',
            bucket='my-bucket',
            region='cn-hangzhou',
            access_key_id='AK', access_key_secret='SK',
        ))
        client.put_object('reports/2026.txt', 'hello')
        client.presigned_url('reports/2026.txt', expires=600)
    """

    oss_type: ClassVar[str] = 'aliyun'

    #: 阿里云用户自定义元数据的请求头前缀（门面层裸键名在此翻译）
    _META_PREFIX: ClassVar[str] = 'x-oss-meta-'

    # region ======== 构造与连接 ========
    def __init__(self, cfg: OssCfg) -> None:
        # oss2.Bucket 句柄按桶名惰性构建并缓存 + 构建锁（防止并发首用时重复构建）
        self._buckets: dict[str, Any] = {}
        self._bucket_lock = threading.Lock()
        super().__init__(cfg)

    def _validate_and_prepare_cfg(self) -> None:
        """
        校验阿里云所需配置并补全可推导默认。

        必填：``bucket`` / ``access_key_id`` / ``access_key_secret``；
        ``endpoint`` 与 ``region`` 至少一个（仅 region 时推导默认 endpoint 并写回）；
        ``endpoint`` 统一去除尾部 ``/``。

        Raises:
            ValueError: 必填字段缺失、或 endpoint/region 均未提供时抛出。
        """
        validation.check_required_fields_not_empty(
            self.cfg, ['bucket', 'access_key_id', 'access_key_secret'], '阿里云OSS配置')
        cfg = self.cfg
        if not cfg.endpoint and not cfg.region:
            raise ValueError(
                "阿里云OSS配置需提供 endpoint 或 region 之一"
                "（如 endpoint='https://oss-cn-hangzhou.aliyuncs.com' 或 region='cn-hangzhou'）"
            )
        if cfg.endpoint:
            cfg.endpoint = cfg.endpoint.rstrip('/')
        else:
            # 仅提供 region：按阿里云公网 endpoint 规则推导并写回 cfg
            assert cfg.region is not None
            cfg.endpoint = f'https://oss-{cfg.region}.aliyuncs.com'

    def get_driver(self) -> Any:
        """
        获取 oss2 SDK 模块（未安装时自动 pip 安装）。

        Returns:
            oss2 模块对象。
        """
        return modutil.import_module('oss2', 'oss2')

    def _get_bucket(self, bucket: str) -> Any:
        """
        获取指定桶的（惰性构建并缓存的）oss2.Bucket 句柄。

        Args:
            bucket: 桶名（非空，由基类经 :meth:`~pykunlun.oss.client.OssClient._effective_bucket`
                解析后传入）。

        Returns:
            绑定该桶名的 ``oss2.Bucket`` 实例，同一客户端实例内按桶名复用。
        """
        handle = self._buckets.get(bucket)
        if handle is None:
            with self._bucket_lock:
                handle = self._buckets.get(bucket)
                if handle is None:
                    oss2 = self.get_driver()
                    cfg = self.cfg
                    # 密钥与 endpoint 已在构造校验时确保非空，断言仅供类型收窄
                    assert cfg.access_key_id is not None
                    assert cfg.access_key_secret is not None
                    assert cfg.endpoint is not None
                    if cfg.security_token:
                        auth = oss2.StsAuth(cfg.access_key_id, cfg.access_key_secret,
                                            cfg.security_token)
                    else:
                        auth = oss2.Auth(cfg.access_key_id, cfg.access_key_secret)
                    handle = oss2.Bucket(auth, cfg.endpoint, bucket)
                    self._buckets[bucket] = handle
                    log.debug("已建立阿里云OSS句柄: bucket=%s endpoint=%s", bucket, cfg.endpoint)
        return handle
    # endregion

    # region ======== 底层钩子实现（首参桶名 + 物理键语义，与基类一一对应） ========
    def _put(self, bucket: str, key: str, data: BinaryIO, content_type: str | None = None,
             metadata: dict[str, str] | None = None) -> None:
        # 二进制流直传（oss2 依 seek 确定 ContentLength，基类传入的流均可 seek）；
        # 流的生命周期归调用方，本方法不关闭
        headers = self._build_put_headers(content_type, metadata)
        self._get_bucket(bucket).put_object(key, data, headers=headers or None)

    def _get(self, bucket: str, key: str) -> BinaryIO:
        # 404 在发起请求时同步抛出（约定时序）；GetObjectResult 本身是二进制流，
        # 由调用方经 contextlib.closing 关闭（oss2 无类型标注，显式收口为 BinaryIO）
        try:
            stream: BinaryIO = self._get_bucket(bucket).get_object(key)
            return stream
        except self._not_found_errors() as e:
            raise FileNotFoundError(f"对象不存在: {key}") from e

    def _delete(self, bucket: str, key: str) -> None:
        # oss2 delete_object 幂等（键不存在同样返回成功），直接透传
        self._get_bucket(bucket).delete_object(key)

    def _head(self, bucket: str, key: str) -> ObjectStat | None:
        try:
            head = self._get_bucket(bucket).head_object(key)
        except self._not_found_errors():
            return None
        content_type = getattr(head, 'content_type', None)
        return ObjectStat(
            key=key,
            size=int(head.content_length),
            last_modified=datetime.fromtimestamp(int(head.last_modified)),
            etag=str(head.etag) if head.etag else None,
            content_type=str(content_type) if content_type else None,
            metadata=self._extract_meta_headers(getattr(head, 'headers', None)) or None,
        )

    def _list(self, bucket: str, prefix: str, delimiter: str | None) -> list[ObjectStat]:
        # ObjectIteratorV2 自动翻页；delimiter 透传给云端做目录层列举（'' 为不启用）。
        # 目录型条目（公共前缀）size/last_modified 为 None，按 ObjectStat 约定置 -1/None
        it = self.get_driver().ObjectIteratorV2(self._get_bucket(bucket), prefix=prefix,
                                                delimiter=delimiter or '')
        stats: list[ObjectStat] = []
        for obj in it:
            stats.append(ObjectStat(
                key=obj.key,
                size=int(obj.size) if obj.size is not None else -1,
                last_modified=(datetime.fromtimestamp(int(obj.last_modified))
                               if obj.last_modified else None),
                etag=str(obj.etag) if obj.etag else None,
            ))
        return stats

    def _copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
        # oss2 copy_object 的源/目标桶本就是两个独立寻址参数，跨桶复制原生支持
        try:
            self._get_bucket(dst_bucket).copy_object(src_bucket, src_key, dst_bucket, dst_key)
        except self._not_found_errors() as e:
            raise FileNotFoundError(f"源对象不存在: {src_key}") from e
    # endregion

    # region ======== 可选钩子覆盖（对应基类"可选钩子"区域） ========
    def _upload_file(self, bucket: str, key: str, local_path: str,
                     content_type: str | None = None,
                     metadata: dict[str, str] | None = None) -> None:
        # 分片断点续传上传：小文件自动退化为普通上传（oss2 内部按阈值分流）
        headers = self._build_put_headers(content_type, metadata)
        self.get_driver().resumable_upload(self._get_bucket(bucket), key, local_path,
                                           part_size=10 * 1024 * 1024,
                                           headers=headers or None)

    def _download_file(self, bucket: str, key: str, local_path: str) -> None:
        # 先探存在性：键不存在时此处即抛，目标目录/文件不受影响（对齐基类默认实现的时序约定），
        # 再建父目录供 resumable_download 落盘
        if self._head(bucket, key) is None:
            raise FileNotFoundError(f"对象不存在: {key}")
        fileutil.make_parent_dirs(local_path)
        try:
            self.get_driver().resumable_download(self._get_bucket(bucket), key, local_path,
                                                 part_size=10 * 1024 * 1024)
        except self._not_found_errors() as e:
            raise FileNotFoundError(f"对象不存在: {key}") from e

    def _presigned_url(self, bucket: str, key: str, expires: int) -> str:
        # sign_url 生成含签名的完整 URL，过期前可匿名 GET
        return str(self._get_bucket(bucket).sign_url('GET', key, expires))
    # endregion

    # region ======== 生命周期 ========
    def close(self) -> None:
        """
        释放缓存的 oss2.Bucket 句柄（连接池随句柄对象交由垃圾回收）。

        清空后如继续操作，句柄会按桶名重新惰性构建。
        """
        with self._bucket_lock:
            self._buckets.clear()
    # endregion

    # region ======== 内部工具 ========
    def _build_put_headers(self, content_type: str | None,
                           metadata: dict[str, str] | None) -> dict[str, str]:
        """
        把门面的 content_type/metadata 翻译为 oss2 的请求头。

        Args:
            content_type: MIME 类型；None 表示不设置。
            metadata: 裸键名的用户元数据；None/空表示不设置，
                键统一加 :attr:`_META_PREFIX` 前缀（阿里云约定）。

        Returns:
            请求头字典（可能为空，调用方按 falsy 透传 None）。
        """
        headers: dict[str, str] = {}
        if content_type:
            headers['Content-Type'] = content_type
        for k, v in (metadata or {}).items():
            headers[f'{self._META_PREFIX}{k}'] = str(v)
        return headers

    def _extract_meta_headers(self, headers: Any) -> dict[str, str]:
        """
        从响应头提取用户自定义元数据（剥除 ``x-oss-meta-`` 前缀还原裸键名）。

        Args:
            headers: ``head_object`` 返回的响应头映射（大小写不敏感字典或普通 dict）。

        Returns:
            裸键名的元数据字典；无用户元数据时为空 dict。
        """
        result: dict[str, str] = {}
        for k, v in dict(headers or {}).items():
            if str(k).lower().startswith(self._META_PREFIX):
                result[str(k)[len(self._META_PREFIX):]] = str(v)
        return result

    def _not_found_errors(self) -> tuple[type[Exception], ...]:
        """
        汇总 oss2 的"对象不存在"异常类型。

        Returns:
            异常类型元组，供 except 子句捕获后转译为 :class:`FileNotFoundError`。
        """
        oss2 = self.get_driver()
        return (oss2.exceptions.NotFound,)
    # endregion

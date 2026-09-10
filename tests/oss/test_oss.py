"""
baibao.oss 对象存储模块的单元测试。

不触网、不依赖真实凭证：
  - :class:`AliyunOssClient` 配置校验（必填、endpoint/region 二选一、region 推导
    endpoint、endpoint 归一、oss_type 一致性）与连接惰性（构造零副作用）；
  - 底层钩子对 oss2 API 的转译逻辑：以自包含 fake driver 注入（monkeypatch
    ``get_driver``），覆盖 put/get/exists/stat/list/copy/move/upload/download/
    presigned_url 与 NotFound → FileNotFoundError 的统一异常约定；
  - :data:`oss_mgr` 装配：配置加载器注册实现类、工厂化创建、本地实例经管理器全流程。
"""

import io
import os
import tempfile
import types
import unittest
from collections.abc import Iterator
from typing import Any

from baibao.oss import AliyunOssClient, LocalOssClient, OssCfg, oss_mgr


# region ======== fake oss2 ========
class _FakeNotFound(Exception):
    """替代 oss2.exceptions.NotFound。"""


class _FakeAuth:
    """替代 oss2.Auth / oss2.StsAuth（记录入参供断言）。"""

    def __init__(self, access_key_id: str, access_key_secret: str,
                 security_token: str | None = None) -> None:
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.security_token = security_token


class _FakeBucket:
    """替代 oss2.Bucket：内存字典模拟对象存取，记录构造入参。"""

    last_auth: _FakeAuth | None = None
    last_endpoint: str | None = None
    last_name: str | None = None

    def __init__(self, auth: _FakeAuth, endpoint: str, bucket_name: str) -> None:
        _FakeBucket.last_auth = auth
        _FakeBucket.last_endpoint = endpoint
        _FakeBucket.last_name = bucket_name
        self.store: dict[str, bytes] = {}
        self.stored_headers: dict[str, dict[str, str]] = {}

    def put_object(self, key: str, data: Any, headers: Any = None) -> None:
        self.store[key] = data if isinstance(data, bytes) else str(data).encode()
        if headers:
            self.stored_headers[key] = {str(k): str(v) for k, v in dict(headers).items()}

    def get_object(self, key: str) -> io.BytesIO:
        if key not in self.store:
            raise _FakeNotFound(key)
        return io.BytesIO(self.store[key])

    def delete_object(self, key: str) -> None:
        self.store.pop(key, None)  # 幂等语义与真实 oss2 一致
        self.stored_headers.pop(key, None)

    def object_exists(self, key: str) -> bool:
        return key in self.store

    def head_object(self, key: str) -> types.SimpleNamespace:
        if key not in self.store:
            raise _FakeNotFound(key)
        return types.SimpleNamespace(
            content_length=len(self.store[key]),
            last_modified=1700000000,
            etag='"fake-etag"',
            content_type=self.stored_headers.get(key, {}).get('Content-Type'),
            headers=dict(self.stored_headers.get(key, {})),
        )

    def copy_object(self, source_bucket_name: str, source_key: str, target_key: str,
                    headers: Any = None, params: Any = None) -> None:
        if source_key not in self.store:
            raise _FakeNotFound(source_key)
        self.store[target_key] = self.store[source_key]
        if source_key in self.stored_headers:  # COPY 语义：元数据随源
            self.stored_headers[target_key] = dict(self.stored_headers[source_key])

    def sign_url(self, method: str, key: str, expires: int,
                 headers: Any = None, params: Any = None,
                 slash_safe: bool = False, additional_headers: Any = None) -> str:
        return f'https://fake.endpoint/{key}?method={method}&e={expires}'


class _FakeObjectIteratorV2:
    """替代 oss2.ObjectIteratorV2：按前缀过滤内存对象并整形为 SimplifiedObjectInfo 风格。"""

    def __init__(self, bucket: _FakeBucket, prefix: str = '', delimiter: str = '',
                 **kwargs: Any) -> None:
        self._bucket = bucket
        self._prefix = prefix

    def __iter__(self) -> Iterator[types.SimpleNamespace]:
        for key in sorted(self._bucket.store):
            if not key.startswith(self._prefix):
                continue
            yield types.SimpleNamespace(
                key=key,
                size=len(self._bucket.store[key]),
                last_modified=1700000000,
                etag='iter-etag',
            )


class _FakeOss2Module:
    """替代 oss2 模块本体（get_driver 的返回值）。"""

    exceptions = types.SimpleNamespace(NotFound=_FakeNotFound)
    Auth = _FakeAuth
    StsAuth = _FakeAuth
    Bucket = _FakeBucket
    ObjectIteratorV2 = _FakeObjectIteratorV2

    @staticmethod
    def resumable_upload(bucket: _FakeBucket, key: str, filename: str,
                         part_size: int | None = None, headers: Any = None,
                         **kwargs: Any) -> None:
        with open(filename, 'rb') as f:
            bucket.put_object(key, f.read(), headers=headers)

    @staticmethod
    def resumable_download(bucket: _FakeBucket, key: str, filename: str,
                           part_size: int | None = None, **kwargs: Any) -> None:
        with open(filename, 'wb') as f:
            f.write(bucket.get_object(key).read())


def _make_aliyun_client(**cfg_overrides: Any) -> AliyunOssClient:
    """构造注入 fake driver 的阿里云客户端（不发真实请求）。"""
    cfg_data: dict[str, Any] = {
        'oss_type': 'aliyun',
        'bucket': 'test-bucket',
        'region': 'cn-hangzhou',
        'access_key_id': 'ak-test',
        'access_key_secret': 'sk-test',
    }
    cfg_data.update(cfg_overrides)
    client = AliyunOssClient(OssCfg(**cfg_data))
    client.get_driver = lambda: _FakeOss2Module()  # type: ignore[method-assign]
    return client
# endregion


# region ======== 配置校验 ========
class TestAliyunCfgValidation(unittest.TestCase):
    """测试 AliyunOssClient 构造期校验（零副作用，不装依赖不联网）。"""

    def test_missing_bucket_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AliyunOssClient(OssCfg(
                oss_type='aliyun', region='cn-hangzhou',
                access_key_id='ak', access_key_secret='sk'))

    def test_missing_keys_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AliyunOssClient(OssCfg(oss_type='aliyun', bucket='b', region='cn-hangzhou'))

    def test_endpoint_and_region_both_missing_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AliyunOssClient(OssCfg(
                oss_type='aliyun', bucket='b',
                access_key_id='ak', access_key_secret='sk'))

    def test_region_derives_endpoint(self) -> None:
        client = AliyunOssClient(OssCfg(
            oss_type='aliyun', bucket='b', region='cn-beijing',
            access_key_id='ak', access_key_secret='sk'))
        assert client.cfg.endpoint == 'https://oss-cn-beijing.aliyuncs.com'

    def test_endpoint_wins_and_trailing_slash_stripped(self) -> None:
        client = AliyunOssClient(OssCfg(
            oss_type='aliyun', bucket='b', region='cn-beijing',
            endpoint='https://oss-cn-hangzhou.aliyuncs.com/',
            access_key_id='ak', access_key_secret='sk'))
        assert client.cfg.endpoint == 'https://oss-cn-hangzhou.aliyuncs.com'

    def test_lazy_connection(self) -> None:
        """构造零副作用：不装 SDK、不建连接，首次操作才构建 Bucket。"""
        client = AliyunOssClient(OssCfg(
            oss_type='aliyun', bucket='b', region='cn-hangzhou',
            access_key_id='ak', access_key_secret='sk'))
        self.assertIsNone(client._bucket)

    def test_oss_type_mismatch_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AliyunOssClient(OssCfg(
                oss_type='local', bucket='b', region='cn-hangzhou',
                access_key_id='ak', access_key_secret='sk'))
# endregion


# region ======== 钩子转译（fake driver 注入） ========
class TestAliyunHooks(unittest.TestCase):
    """测试底层钩子对 oss2 API 的转译（fake driver，不发请求）。"""

    def setUp(self) -> None:
        self.client = _make_aliyun_client()

    def test_put_get_roundtrip(self) -> None:
        self.client.put_object('a/b.txt', '你好')
        self.assertEqual(self.client.get_object_text('a/b.txt'), '你好')

    def test_get_missing_translates_to_file_not_found(self) -> None:
        """oss2 NotFound 统一转译为内置 FileNotFoundError（跨实现约定）。"""
        with self.assertRaises(FileNotFoundError):
            self.client.get_object('missing')

    def test_delete_idempotent(self) -> None:
        self.client.put_object('k', 'v')
        self.client.delete_object('k')
        self.assertFalse(self.client.exists('k'))
        self.client.delete_object('k')  # 再删不抛

    def test_stat_shaped(self) -> None:
        self.client.put_object('k.txt', '12345')
        st = self.client.stat('k.txt')
        assert st is not None
        self.assertEqual(st.key, 'k.txt')
        self.assertEqual(st.size, 5)
        self.assertIsNotNone(st.last_modified)
        self.assertEqual(st.etag, '"fake-etag"')
        self.assertIsNone(self.client.stat('missing'))

    def test_list_prefix(self) -> None:
        self.client.put_object('docs/a.txt', 'a')
        self.client.put_object('docs/sub/b.txt', 'b')
        self.client.put_object('imgs/c.png', b'c')
        self.assertEqual(self.client.list_keys('docs/'), ['docs/a.txt', 'docs/sub/b.txt'])

    def test_copy_move(self) -> None:
        self.client.put_object('src', 'v')
        self.client.copy_object('src', 'dst')
        self.client.move_object('src', 'dst2')
        self.assertEqual(self.client.get_object_text('dst'), 'v')
        self.assertEqual(self.client.get_object_text('dst2'), 'v')
        self.assertFalse(self.client.exists('src'))
        with self.assertRaises(FileNotFoundError):
            self.client.copy_object('missing', 'x')

    def test_upload_download_files(self) -> None:
        fd, src = tempfile.mkstemp(suffix='.bin')
        os.close(fd)
        dst = src + '.down'
        try:
            with open(src, 'wb') as f:
                f.write(b'\x01\x02\x03')
            self.client.upload_file('files/f.bin', src)
            self.client.download_file('files/f.bin', dst)
            with open(dst, 'rb') as f:
                self.assertEqual(f.read(), b'\x01\x02\x03')
        finally:
            for p in (src, dst):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_download_missing_translates(self) -> None:
        # 目标文件放进临时目录：fake download 在 open 之后才因对象不存在抛错，
        # 不能把空文件残留在 pytest 的工作目录
        fd, out = tempfile.mkstemp(suffix='.bin')
        os.close(fd)
        try:
            with self.assertRaises(FileNotFoundError):
                self.client.download_file('missing', out)
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

    def test_presigned_url(self) -> None:
        self.client.put_object('k', 'v')
        url = self.client.presigned_url('k', expires=600)
        self.assertIn('k', url)
        self.assertIn('e=600', url)

    def test_bucket_built_lazily_with_expected_args(self) -> None:
        self.client.put_object('k', 'v')
        self.assertEqual(_FakeBucket.last_name, 'test-bucket')
        self.assertEqual(_FakeBucket.last_endpoint, 'https://oss-cn-hangzhou.aliyuncs.com')
        self.assertEqual(_FakeBucket.last_auth.access_key_id, 'ak-test')

    def test_sts_token_uses_sts_auth(self) -> None:
        client = _make_aliyun_client(security_token='token-x')
        client.put_object('k', 'v')
        assert _FakeBucket.last_auth is not None
        self.assertEqual(_FakeBucket.last_auth.security_token, 'token-x')

    def test_prefix_applied_to_physical_keys(self) -> None:
        client = _make_aliyun_client(prefix='tenant-a')
        client.put_object('k.txt', 'v')
        # 物理键带前缀，逻辑键透明
        self.assertIn('tenant-a/k.txt', client._get_bucket().store)
        self.assertEqual(client.list_keys(), ['k.txt'])
# endregion


# region ======== 元数据（content_type / metadata） ========
class TestAliyunMetadata(unittest.TestCase):
    """测试阿里云实现的元数据翻译（Content-Type + x-oss-meta-* 前缀）。"""

    def setUp(self) -> None:
        self.client = _make_aliyun_client()

    def test_put_translates_headers(self) -> None:
        """裸键名元数据加 x-oss-meta- 前缀，content_type 设 Content-Type。"""
        self.client.put_object('k', 'v', content_type='text/plain',
                               metadata={'author': 'kahle', 'Origin-App': 'baibao'})
        bucket = self.client._get_bucket()
        headers = bucket.stored_headers['k']
        self.assertEqual(headers['Content-Type'], 'text/plain')
        self.assertEqual(headers['x-oss-meta-author'], 'kahle')
        self.assertEqual(headers['x-oss-meta-Origin-App'], 'baibao')
        self.assertNotIn('author', headers)  # 裸键名不出现在请求头

    def test_put_without_meta_passes_no_headers(self) -> None:
        """不带元数据时 headers 透传 None（不发 Content-Type）。"""
        self.client.put_object('k', 'v')
        self.assertNotIn('k', self.client._get_bucket().stored_headers)

    def test_stat_reads_back(self) -> None:
        """stat 读回 content_type 与剥除前缀后的裸键名元数据。"""
        self.client.put_object('k', 'v', content_type='application/json',
                               metadata={'author': 'kahle'})
        st = self.client.stat('k')
        assert st is not None
        self.assertEqual(st.content_type, 'application/json')
        self.assertEqual(st.metadata, {'author': 'kahle'})

    def test_stat_without_meta_returns_none_fields(self) -> None:
        self.client.put_object('k', 'v')
        st = self.client.stat('k')
        assert st is not None
        self.assertIsNone(st.content_type)
        self.assertIsNone(st.metadata)

    def test_upload_file_carries_headers(self) -> None:
        """upload_file 的 content_type/metadata 经 resumable_upload 的 headers 生效。"""
        fd, src = tempfile.mkstemp(suffix='.bin')
        os.close(fd)
        try:
            with open(src, 'wb') as f:
                f.write(b'x')
            self.client.upload_file('u.bin', src, content_type='image/png',
                                    metadata={'tag': 'demo'})
            headers = self.client._get_bucket().stored_headers['u.bin']
            self.assertEqual(headers['Content-Type'], 'image/png')
            self.assertEqual(headers['x-oss-meta-tag'], 'demo')
        finally:
            os.remove(src)
# endregion


# region ======== oss_mgr 装配 ========
def _reset_registry() -> None:
    """清空模块级管理器的所有已注册实例（不影响类注册表缓存语义）。"""
    for name in list(oss_mgr.get_registered_names()):
        oss_mgr.unregister(name)


class TestOssMgr(unittest.TestCase):
    """测试模块级 oss_mgr 的装配与转发。"""

    def setUp(self) -> None:
        _reset_registry()
        self._tmpdir = tempfile.mkdtemp(prefix='baibao-oss-test-')
        loader = oss_mgr.get_config_loader()
        assert loader is not None
        loader(oss_mgr, '')  # 触发配置加载：注册 local/aliyun 实现类

    def tearDown(self) -> None:
        _reset_registry()

    def test_loader_registers_client_classes(self) -> None:
        types_registered = oss_mgr.get_registered_client_types()
        self.assertIn('local', types_registered)
        self.assertIn('aliyun', types_registered)

    def test_factory_create_local_via_cfg(self) -> None:
        root = os.path.join(self._tmpdir, 'root')
        os.makedirs(root, exist_ok=True)
        oss_mgr.register('default', OssCfg(oss_type='local', root=root))
        self.assertIsInstance(oss_mgr.get_client(), LocalOssClient)
        # 经管理器全流程
        oss_mgr.put_object('m/k.txt', 'hello')
        self.assertEqual(oss_mgr.get_object_text('m/k.txt'), 'hello')
        self.assertEqual(oss_mgr.list_keys('m/'), ['m/k.txt'])
        oss_mgr.delete_object('m/k.txt')
        self.assertEqual(oss_mgr.list_keys(), [])

    def test_unknown_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            oss_mgr.get_client('no-such-instance')
# endregion


if __name__ == '__main__':
    unittest.main()

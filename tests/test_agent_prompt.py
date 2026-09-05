"""agent_prompt 的单元测试：纯函数（解析/渲染/交换格式）+ sqlite 临时库的存储层。"""

import os
import tempfile
import unittest

from baibao.ai_agent.prompt import (
    RdbPromptStore,
    markdown_to_template,
    parse_blocks,
    parse_variables,
    render_template,
    template_to_markdown,
)
from baibao.db.rdb import RdbCfg, SqliteClient, rdb_mgr

# region ======== 纯函数：解析与渲染 ========

class TestParseVariables(unittest.TestCase):
    """parse_variables：变量提取（去重、保序）"""

    def test_extract_ordered_unique(self) -> None:
        content = '用 {{language}} 写 {{style}} 的 {{language}} 代码'
        self.assertEqual(parse_variables(content), ['language', 'style'])

    def test_no_variables(self) -> None:
        self.assertEqual(parse_variables('没有任何变量'), [])

    def test_spaces_inside_braces(self) -> None:
        self.assertEqual(parse_variables('a {{ name }} b'), ['name'])


class TestParseBlocks(unittest.TestCase):
    """parse_blocks：可选块解析与校验"""

    def test_basic(self) -> None:
        content = ('A\n'
                   '<!-- @block:cc | default:off | 仅并发场景保留 -->\n'
                   'B\n'
                   '<!-- @endblock:cc -->\n'
                   'C')
        blocks = parse_blocks(content)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['name'], 'cc')
        self.assertFalse(blocks[0]['default_on'])
        self.assertEqual(blocks[0]['note'], '仅并发场景保留')

    def test_default_on_when_unspecified(self) -> None:
        content = '<!-- @block:x -->y<!-- @endblock:x -->'
        blocks = parse_blocks(content)
        self.assertTrue(blocks[0]['default_on'])
        self.assertEqual(blocks[0]['note'], '')

    def test_mismatched_end(self) -> None:
        with self.assertRaises(ValueError):
            parse_blocks('<!-- @block:a --><!-- @endblock:b -->')

    def test_nested_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_blocks('<!-- @block:a --><!-- @block:b --><!-- @endblock:b --><!-- @endblock:a -->')

    def test_unclosed_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_blocks('<!-- @block:a -->正文')

    def test_end_without_start(self) -> None:
        with self.assertRaises(ValueError):
            parse_blocks('正文<!-- @endblock:a -->')


class TestRenderTemplate(unittest.TestCase):
    """render_template：填变量/裁剪块/剥离标记"""

    CONTENT = (
        '请用 {{language}} 完成代码审查。\n'
        '<!-- @block:concurrent | default:off | 仅并发场景保留 -->\n'
        '重点检查 {{framework}} 并发安全。\n'
        '<!-- @endblock:concurrent -->\n'
        '<!-- @block:perf | default:on | 性能敏感场景保留 -->\n'
        '关注热点路径性能。\n'
        '<!-- @endblock:perf -->\n'
        '输出审查报告。'
    )

    def test_fill_and_default_blocks(self) -> None:
        out = render_template(self.CONTENT, values={'language': 'Java', 'framework': 'JUC'})
        # concurrent 默认 off 被删，perf 默认 on 保留；标记全部剥离；变量填充
        self.assertIn('请用 Java 完成代码审查。', out)
        self.assertIn('关注热点路径性能。', out)
        self.assertIn('输出审查报告。', out)
        self.assertNotIn('@block', out)
        self.assertNotIn('并发安全', out)
        self.assertNotIn('{{', out)

    def test_include_off_block(self) -> None:
        out = render_template(self.CONTENT, values={'language': 'Go', 'framework': 'goroutine'},
                              include={'concurrent'})
        self.assertIn('重点检查 goroutine 并发安全。', out)

    def test_exclude_on_block(self) -> None:
        out = render_template(self.CONTENT, values={'language': 'Go', 'framework': 'x'},
                              exclude={'perf'})
        self.assertNotIn('热点路径', out)

    def test_missing_required_variable(self) -> None:
        # framework 只在默认关闭的块内：块被裁掉时不要求该变量
        out = render_template(self.CONTENT, values={'language': 'Java'})
        self.assertNotIn('{{', out)
        # 但显式 include 该块后，缺少必填变量要报错并列出
        with self.assertRaises(ValueError) as cm:
            render_template(self.CONTENT, values={'language': 'Java'},
                            include={'concurrent'})
        self.assertIn('framework', str(cm.exception))

    def test_include_exclude_conflict(self) -> None:
        with self.assertRaises(ValueError):
            render_template(self.CONTENT, values={'language': 'Java', 'framework': 'x'},
                            include={'perf'}, exclude={'perf'})

    def test_unknown_block(self) -> None:
        with self.assertRaises(ValueError) as cm:
            render_template(self.CONTENT, values={'language': 'Java', 'framework': 'x'},
                            include={'no-such-block'})
        self.assertIn('no-such-block', str(cm.exception))

    def test_optional_meta_default(self) -> None:
        content = '部署 {{env}} 环境'
        meta = [{'name': 'env', 'required': False, 'default': 'test'}]
        out = render_template(content, values={}, meta=meta)
        self.assertEqual(out, '部署 test 环境')

    def test_optional_meta_without_default_keeps_placeholder(self) -> None:
        content = '部署 {{env}} 环境'
        meta = [{'name': 'env', 'required': False}]
        out = render_template(content, values={}, meta=meta)
        self.assertEqual(out, '部署 {{env}} 环境')

    def test_removed_block_collapses_blank_lines(self) -> None:
        content = '头\n\n<!-- @block:x | default:off -->\n中间\n<!-- @endblock:x -->\n\n尾'
        out = render_template(content, values={})
        self.assertNotIn('中间', out)
        self.assertNotIn('\n\n\n', out)
        self.assertIn('头', out)
        self.assertIn('尾', out)


class TestMarkdownExchange(unittest.TestCase):
    """template_to_markdown / markdown_to_template 交换格式往返"""

    def test_round_trip(self) -> None:
        row = {'name': 'code-review', 'title': '代码审查', 'description': '通用审查',
               'tags': 'review,quality',
               'vars': [{'name': 'language', 'required': True}],
               'content': '用 {{language}} 审查\n<!-- @block:c | default:off -->x<!-- @endblock:c -->'}
        text = template_to_markdown(row)
        tpl = markdown_to_template(text)
        self.assertEqual(tpl['name'], 'code-review')
        self.assertEqual(tpl['title'], '代码审查')
        self.assertEqual(tpl['description'], '通用审查')
        self.assertEqual(tpl['tags'], 'review,quality')
        self.assertEqual(tpl['vars'], [{'name': 'language', 'required': True}])
        self.assertIn('@block:c', tpl['content'])

    def test_missing_frontmatter(self) -> None:
        with self.assertRaises(ValueError):
            markdown_to_template('没有 frontmatter 的裸文本')

    def test_missing_required_key(self) -> None:
        with self.assertRaises(ValueError):
            markdown_to_template('---\nname: only-name\n---\n正文')

    def test_invalid_vars_ignored(self) -> None:
        tpl = markdown_to_template('---\nname: a\ntitle: t\nvars: {bad\n---\n正文')
        self.assertIsNone(tpl['vars'])


# endregion


# region ======== 存储层：sqlite 临时库 ========

def _reset_registry() -> None:
    """清空模块级管理器的所有已注册实例。"""
    for name in list(rdb_mgr.get_registered_names()):
        rdb_mgr.unregister(name)


class _PromptStoreTestBase(unittest.TestCase):
    """存储层测试基类：每个用例使用独立的临时 sqlite 文件库（注册为 default 实例）。"""

    def setUp(self) -> None:
        _reset_registry()
        self._fd, self._db_path = tempfile.mkstemp(suffix='.db')
        os.close(self._fd)
        rdb_mgr.register('default', SqliteClient(RdbCfg(db_type='sqlite', database=self._db_path)))

    def tearDown(self) -> None:
        _reset_registry()
        try:
            os.remove(self._db_path)
        except OSError:
            pass

    @staticmethod
    def _make_store(owner: str | None = None) -> RdbPromptStore:
        store = RdbPromptStore(db_name='default', owner=owner)
        store.init_store()
        return store


class TestSave(_PromptStoreTestBase):
    """save：入库/查重/覆盖/归属"""

    def test_save_shared_and_personal(self) -> None:
        alice = self._make_store('alice')
        shared = alice.save('java-review', 'Java 审查', '正文 {{language}}', shared=True)
        self.assertEqual(shared['action'], 'inserted')
        self.assertIsNone(shared['row']['owner'])
        personal = alice.save('my-note', '个人笔记', '正文', shared=False)
        self.assertEqual(personal['row']['owner'], 'alice')
        # 自动扫描变量
        self.assertEqual([v['name'] for v in shared['row']['vars']], ['language'])

    def test_save_personal_without_owner_rejected(self) -> None:
        store = self._make_store()
        with self.assertRaises(ValueError):
            store.save('x', '无身份个人模板', '正文', shared=False)

    def test_duplicate_rejected_then_force_updates(self) -> None:
        store = self._make_store('alice')
        store.save('dup', '标题', 'v1', shared=True)
        with self.assertRaises(ValueError) as cm:
            store.save('dup', '标题', 'v2', shared=True)
        self.assertIn('--force', str(cm.exception))
        updated = store.save('dup', '标题', 'v2', shared=True, force=True)
        self.assertEqual(updated['action'], 'updated')
        self.assertEqual(updated['row']['content'], 'v2')
        dup = store.find_by_name('dup', any_owner=True)
        assert dup is not None
        self.assertEqual(updated['row']['id'], dup['id'])

    def test_force_overwrite_shared_requires_shared_flag(self) -> None:
        alice = self._make_store('alice')
        alice.save('shared-tpl', '共享', 'v1', shared=True)
        # 个人角色覆盖共享模板 → 拒绝
        with self.assertRaises(ValueError):
            alice.save('shared-tpl', '共享', 'v2', shared=False, force=True)
        # 显式 shared → 允许
        alice.save('shared-tpl', '共享', 'v2', shared=True, force=True)
        tpl = alice.find_by_name('shared-tpl')
        assert tpl is not None
        self.assertEqual(tpl['content'], 'v2')

    def test_save_with_bad_block_markers_rejected(self) -> None:
        store = self._make_store('alice')
        # 未闭合块
        with self.assertRaises(ValueError) as cm:
            store.save('bad1', '坏标记', '正文 <!-- @block:x -->未闭合', shared=True)
        self.assertIn('缺少尾标记', str(cm.exception))
        # 头尾不匹配
        with self.assertRaises(ValueError) as cm:
            store.save('bad2', '坏标记', '<!-- @block:a --><!-- @endblock:b -->', shared=True)
        self.assertIn('不匹配', str(cm.exception))
        # 均未入库
        self.assertEqual(store.count(), 0)

    def test_force_overwrite_others_personal_rejected(self) -> None:
        alice = self._make_store('alice')
        alice.save('mine', '个人', 'v1')
        bob = self._make_store('bob')
        with self.assertRaises(ValueError):
            bob.save('mine', '个人', 'v2', force=True)
        with self.assertRaises(ValueError):
            bob.save('mine', '个人', 'v2', shared=True, force=True)


class TestVisibility(_PromptStoreTestBase):
    """可见性：共享人人可见，个人仅本人可见"""

    def setUp(self) -> None:
        super().setUp()
        alice = self._make_store('alice')
        alice.save('shared-tpl', '共享', 's', shared=True)
        alice.save('private-tpl', '个人', 'p')

    def test_owner_sees_both(self) -> None:
        alice = self._make_store('alice')
        self.assertIsNotNone(alice.find_by_name('shared-tpl'))
        self.assertIsNotNone(alice.find_by_name('private-tpl'))
        self.assertEqual(alice.count(), 2)

    def test_other_sees_shared_only(self) -> None:
        bob = self._make_store('bob')
        self.assertIsNotNone(bob.find_by_name('shared-tpl'))
        self.assertIsNone(bob.find_by_name('private-tpl'))
        self.assertEqual(bob.count(), 1)

    def test_anonymous_sees_shared_only(self) -> None:
        anon = self._make_store()
        self.assertIsNotNone(anon.find_by_name('shared-tpl'))
        self.assertIsNone(anon.find_by_name('private-tpl'))


class TestUpdateForgetTouch(_PromptStoreTestBase):
    """update / forget / touch"""

    def test_update_own_personal(self) -> None:
        store = self._make_store('alice')
        store.save('t1', '标题', 'v1')
        self.assertTrue(store.update('t1', {'title': '新标题', 'content': 'v2'}))
        row = store.find_by_name('t1')
        assert row is not None
        self.assertEqual(row['title'], '新标题')
        self.assertEqual(row['content'], 'v2')
        self.assertEqual(row['updated_by'], 'alice')

    def test_update_shared_requires_shared_mode(self) -> None:
        store = self._make_store('alice')
        store.save('t1', '标题', 'v1', shared=True)
        # 个人角色改共享 → 未命中
        self.assertFalse(store.update('t1', {'title': 'x'}, shared_mode=False))
        self.assertTrue(store.update('t1', {'title': 'x'}, shared_mode=True))

    def test_update_rejects_unknown_fields(self) -> None:
        store = self._make_store('alice')
        store.save('t1', '标题', 'v1')
        self.assertFalse(store.update('t1', {'owner': 'bob'}))

    def test_update_with_bad_content_rejected(self) -> None:
        store = self._make_store('alice')
        store.save('t1', '标题', 'v1')
        with self.assertRaises(ValueError):
            store.update('t1', {'content': '正文 <!-- @block:x -->未闭合'})
        # 校验失败不留半更新
        row = store.find_by_name('t1')
        assert row is not None
        self.assertEqual(row['content'], 'v1')

    def test_forget_soft_delete(self) -> None:
        store = self._make_store('alice')
        rid = store.save('t1', '标题', 'v1')['row']['id']
        self.assertTrue(store.forget('t1'))
        self.assertIsNone(store.find_by_name('t1'))
        self.assertEqual(store.count(), 0)
        # 软删行仍在，any_owner + include_deleted 可见
        self.assertIsNotNone(store.find_by_name('t1', include_deleted=True, any_owner=True))
        self.assertEqual(store.count(include_deleted=True), 1)
        del rid  # rid 仅用于确认 save 返回结构

    def test_touch_increments(self) -> None:
        store = self._make_store('alice')
        rid = store.save('t1', '标题', 'v1')['row']['id']
        store.touch(rid)
        store.touch(rid)
        row = store.find_by_name('t1')
        assert row is not None
        self.assertEqual(row['use_count'], 2)


class TestSearchListStats(_PromptStoreTestBase):
    """search / list / stats / tag_counts"""

    def setUp(self) -> None:
        super().setUp()
        store = self._make_store('alice')
        store.save('code-review', '代码审查', '审查 {{language}} 代码', tags='review', shared=True)
        store.save('doc-gen', '文档生成', '给模块生成技术文档', tags='doc', shared=True)
        store.save('api-test', '接口测试', '调接口验证', tags='test,api', shared=True)

    def test_search_name_hit_ranks_first(self) -> None:
        store = self._make_store('alice')
        rows = store.search('code review')
        self.assertEqual(rows[0]['name'], 'code-review')
        self.assertTrue(rows[0]['_score'] > 0)

    def test_search_content_hit(self) -> None:
        store = self._make_store('alice')
        rows = store.search('技术文档')
        self.assertEqual([r['name'] for r in rows], ['doc-gen'])

    def test_search_tag_filter(self) -> None:
        store = self._make_store('alice')
        rows = store.search('测试', tag='api')
        self.assertEqual([r['name'] for r in rows], ['api-test'])

    def test_list_tag_filter_and_hot_order(self) -> None:
        store = self._make_store('alice')
        doc = store.find_by_name('doc-gen')
        assert doc is not None
        store.touch(doc['id'])
        rows = store.list_templates()
        self.assertEqual(rows[0]['name'], 'doc-gen')  # use_count=1 排最前
        rows = store.list_templates(tag='test')
        self.assertEqual([r['name'] for r in rows], ['api-test'])

    def test_stats_and_tag_counts(self) -> None:
        store = self._make_store('alice')
        api = store.find_by_name('api-test')
        assert api is not None
        store.touch(api['id'])
        stats = store.stats()
        self.assertEqual(stats[0]['name'], 'api-test')
        tags = {t['tag']: t['count'] for t in store.tag_counts()}
        self.assertEqual(tags, {'review': 1, 'doc': 1, 'test': 1, 'api': 1})


class TestAttachParsed(_PromptStoreTestBase):
    """find_by_name 附带解析结果：vars / blocks"""

    def test_attached_vars_and_blocks(self) -> None:
        store = self._make_store('alice')
        store.save('t1', '标题',
                   '正文 {{a}} {{b}}\n<!-- @block:x | default:off | 说明 -->内容<!-- @endblock:x -->')
        row = store.find_by_name('t1')
        assert row is not None
        self.assertEqual([v['name'] for v in row['vars']], ['a', 'b'])
        self.assertEqual(row['blocks'],
                         [{'name': 'x', 'default_on': False, 'note': '说明'}])

    def test_attached_parse_error_tolerated(self) -> None:
        # 历史坏数据（写入强校验上线前入库的未闭合块）：读取容错，blocks 显示 parse_error
        store = self._make_store('alice')
        rdb_mgr.execute(
            'INSERT INTO ai_prompt_template (name, title, content, is_deleted) '
            'VALUES (?, ?, ?, 0)',
            ('legacy-bad', '历史坏数据', '正文 <!-- @block:x -->未闭合'),
            name='default')
        row = store.find_by_name('legacy-bad')
        assert row is not None
        self.assertIn('parse_error', row['blocks'][0])

# endregion


if __name__ == '__main__':
    unittest.main()

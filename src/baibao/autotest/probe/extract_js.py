"""
extract_js — 注入页面的 DOM 提取脚本（纯数据，无 Python 逻辑）。

``EXTRACT_JS`` 在已登录页面上执行，返回结构化 dict：可见的 Element Plus 表单项（label/类型/当前值/placeholder/选项/弹窗作用域）、表格（列头/行数/ 首行）、分页、按钮、打开中的弹窗/抽屉、消息、校验错误、页签、展开中的下拉面板（含实际选项）。所有元素经过可见性过滤与截断。

由 :func:`baibao.autotest.probe.runner.extract_summary` 注入执行；
渲染见 :mod:`baibao.autotest.probe.render`。
"""

__all__ = ["EXTRACT_JS"]

# 注入页面的提取脚本：返回结构化 dict（元素全部经过可见性过滤与截断）。
EXTRACT_JS = """() => {
  const visible = el => !!(el && el.getClientRects().length > 0);
  const txt = el => { if (!el) return ''; return (el.textContent || '').replace(/\\s+/g, ' ').trim(); };
  const clip = (s, n) => { s = s || ''; return s.length > n ? s.slice(0, n) + '…' : s; };
  const scopeOf = el => {
    const d = el.closest('.el-dialog');
    if (d && visible(d)) return '弹窗「' + (clip(txt(d.querySelector('.el-dialog__title')), 20) || '无标题') + '」';
    const w = el.closest('.el-drawer');
    if (w && visible(w)) return '抽屉「' + (clip(txt(w.querySelector('.el-drawer__header, .el-drawer__title')), 20) || '无标题') + '」';
    return '';
  };

  const forms = [];
  document.querySelectorAll('.el-form-item').forEach(item => {
    if (!visible(item) || forms.length >= 40) return;
    const label = txt(item.querySelector('.el-form-item__label'));
    const q = s => item.querySelector(s);
    let type = '未知', value = '', options = null;
    if (q('.el-select__wrapper') || q('.el-select')) {
      type = 'select';
      const box = q('.el-select__wrapper') || q('.el-select');
      const tags = Array.from(box.querySelectorAll('.el-tag')).map(e => clip(txt(e), 12));
      if (tags.length) { value = tags.join('、'); }
      else {
        value = txt(box.querySelector('.el-select__selected-item'))
          || (box.querySelector('input') ? box.querySelector('input').value : '');
      }
    } else if (q('.el-cascader')) { type = '级联'; }
    else if (q('.el-range-editor')) {
      type = '日期范围';
      value = Array.from(item.querySelectorAll('.el-range-editor input')).map(i => i.value).filter(Boolean).join(' ~ ');
    } else if (q('.el-date-editor')) {
      type = '日期';
      const i = q('.el-date-editor input'); value = i ? i.value : '';
    } else if (q('.el-input-number')) {
      type = '数字';
      const i = q('input'); value = i ? i.value : '';
    } else if (q('.el-radio-group')) {
      type = '单选';
      options = Array.from(item.querySelectorAll('.el-radio__label')).map(e => txt(e)).slice(0, 8);
    } else if (q('.el-checkbox-group')) {
      type = '多选';
      options = Array.from(item.querySelectorAll('.el-checkbox__label')).map(e => txt(e)).slice(0, 8);
    } else if (q('.el-switch')) {
      type = '开关';
      value = q('.el-switch').classList.contains('is-checked') ? '开' : '关';
    } else if (q('textarea')) {
      type = '多行文本';
      value = q('textarea').value;
    } else if (q('input[type=file]')) { type = '文件'; }
    else if (q('input')) { type = '文本'; value = q('input').value; }
    const input = q('input, textarea');
    const ph = (input && input.placeholder)
      || txt(item.querySelector('.el-select__placeholder'));
    // 搜索栏等无 label 表单项：placeholder 即字段语义，提升为标签
    forms.push({
      label: clip(label || ph, 20),
      required: item.classList.contains('is-required'),
      type: type, value: clip(value, 30),
      placeholder: clip(ph || '', 20),
      options: options, scope: scopeOf(item),
    });
  });

  const tables = [];
  document.querySelectorAll('.el-table').forEach(t => {
    if (!visible(t) || tables.length >= 3) return;
    const cols = Array.from(t.querySelectorAll('.el-table__header th .cell'))
      .map(c => txt(c)).filter(Boolean).slice(0, 15);
    const rows = Array.from(t.querySelectorAll('.el-table__row')).filter(visible);
    const firstRow = rows.length
      ? Array.from(rows[0].querySelectorAll('.cell')).map(c => clip(txt(c), 20)).slice(0, 15)
      : [];
    tables.push({ cols: cols, rowCount: rows.length, firstRow: firstRow });
  });

  const pagination = Array.from(document.querySelectorAll('.el-pagination__total'))
    .filter(visible).map(e => txt(e));

  const buttons = [];
  document.querySelectorAll('button').forEach(b => {
    if (!visible(b) || buttons.length >= 20) return;
    const t = txt(b);
    if (t && !buttons.includes(t)) buttons.push(clip(t, 12));
  });

  const overlays = [];
  document.querySelectorAll('.el-dialog').forEach(d => {
    if (visible(d)) overlays.push('弹窗「' + (clip(txt(d.querySelector('.el-dialog__title')), 20) || '无标题') + '」');
  });
  document.querySelectorAll('.el-drawer').forEach(d => {
    if (visible(d)) overlays.push('抽屉「' + (clip(txt(d.querySelector('.el-drawer__header, .el-drawer__title')), 20) || '无标题') + '」');
  });

  const messages = Array.from(document.querySelectorAll('.el-message')).filter(visible).map(m => {
    const cls = m.className || '';
    const kind = cls.indexOf('--error') >= 0 ? 'error' : (cls.indexOf('--success') >= 0 ? 'success' : 'info');
    return kind + ': ' + clip(txt(m), 50);
  });

  const errors = Array.from(document.querySelectorAll('.el-form-item__error'))
    .filter(visible).map(e => clip(txt(e), 40)).slice(0, 10);

  const tabs = Array.from(document.querySelectorAll('.el-tabs__item'))
    .filter(visible).map(t => clip(txt(t), 12)).slice(0, 15);

  const dropdowns = [];
  document.querySelectorAll('.el-select-dropdown').forEach(dd => {
    if (!visible(dd)) return;
    const items = Array.from(dd.querySelectorAll('.el-select-dropdown__item')).filter(visible);
    const emptyEl = dd.querySelector('.el-select-dropdown__empty, .el-select__empty, .el-select-dropdown__loading');
    dropdowns.push({
      count: items.length,
      emptyText: visible(emptyEl) ? clip(txt(emptyEl), 20) : '',
      selected: items.filter(i => i.classList.contains('selected')).map(i => clip(txt(i), 20)).slice(0, 3),
      texts: items.map(i => clip(txt(i), 20)).filter(Boolean).slice(0, 30),
    });
  });

  return {
    title: clip(document.title, 40),
    url: location.href,
    forms: forms, tables: tables, pagination: pagination, buttons: buttons,
    overlays: overlays, messages: messages, errors: errors, tabs: tabs,
    dropdowns: dropdowns,
  };
}"""

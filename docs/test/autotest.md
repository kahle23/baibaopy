# autotest — Playwright E2E 测试基础设施

把日常 Web 自动化测试中反复用到的能力封装成简洁 API：页面对象基类、浏览器启动、
登录态缓存、接口基类、可选启用的 pytest fixture。

源自实战项目，解决 Vue 3 + Element Plus 后台的 E2E 自动化测试硬骨头。

<br />

## 安装

```bash
# 安装 baibao 与 autotest 可选依赖（playwright / python-dotenv / faker 等）
python -m pip install "baibao[autotest]" -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 可选：安装 Playwright 内置浏览器内核（本地已有 Chrome 或 Edge 可跳过）
python -m playwright install chromium
```

> `faker` 在国内默认 PyPI 源可能装不上，务必用清华源。
>
> 浏览器选择链（全自动，一般无需预装）：**Playwright 内置 Chromium 优先**
> （自动化事件兼容性最好）——未安装时自动下载（有头装完整内核，无头仅装
> 轻量 headless-shell 约 90MB；npmmirror 镜像优先、官方源回退）；下载失败
> 回退本地 Chrome → Edge。启动器只启 Chromium 系；Firefox/WebKit 内核的
> Page 亦可使用本包其余设施（真实输入由 `core.devtools` 按内核分流）。
> `USE_BUILTIN_CHROMIUM` 三态：未设置=自动 / `true`=强制内置 / `false`=仅本地；
> `BAIBAO_BROWSER_AUTO_INSTALL=false` 关闭自动下载；`PLAYWRIGHT_DOWNLOAD_HOST`
> 指定下载源。

<br />

## 分包结构

```
baibao.autotest
├── core/       基础设施层（只被上层依赖）
│   ├── browser.py      浏览器启动（内置 Chromium 优先 + 本地 Chrome/Edge 兜底）
│   ├── login_state.py  登录流程 LoginCfg + 登录态缓存（TTL + 身份绑定）
│   ├── devtools.py     浏览器开发工具协议薄封装：real_click 真实输入按内核分流
│   │                   （Chromium 走 CDP；Firefox/WebKit 走 page.mouse 等价序列）
│   ├── polling.py      通用轮询 poll_until（异步刷新/异步渲染等待统一用它）
│   └── envutil.py      .env 加载 + base_url 规范化
├── page/       页面对象层：base.py 的 BasePage（Element Plus 组件操作）
├── api/        接口层：base.py 的 ApiBase（复用登录态 cookie 的 HTTP 骨架）
├── probe/      DOM 摘要探针：runner(编排) + extract_js(提取脚本) + render(渲染) + url(地址规范化)
└── fixtures/   opt-in pytest fixture + conftest_template 角色级样板

依赖方向：core ← page / api / probe / fixtures ← 包门面 __init__，无循环。
```

**旧路径兼容**：`baibao.autotest.{page, api, fixtures}` 三条旧路径由同名子包
原生接管（导入写法不变）；`browser` / `login_state` / `dom_summary` 三条由根
目录 shim 继续保底（标注 deprecated，后续请切换到 `core.browser` /
`core.login_state` / `probe`）。

<br />

## 模块概览

| 模块 | 关键符号 | 用途 |
|------|----------|------|
| `baibao.autotest.page` | `BasePage` | 页面对象基类：Element Plus 组件操作 + 真实输入 + 对话框作用域 + 表格行列助手 |
| `baibao.autotest.core.browser` | `detect_chrome_path` / `launch_browser` | 本地 Chrome 路径探测与浏览器启动 |
| `baibao.autotest.core.login_state` | `LoginCfg` / `do_login` / `save_storage_state` / `is_auth_valid` | 数据驱动登录流程 + 登录态缓存（TTL） |
| `baibao.autotest.core.devtools` | `real_click` / `new_session` / `engine_name` | 真实输入与 CDP 会话统一收口（跨内核） |
| `baibao.autotest.core.polling` | `poll_until` | 通用轮询等待 |
| `baibao.autotest.api` | `ApiBase` | 后端接口基类：复用浏览器登录态 cookie + 防御式 JSON 解析 |
| `baibao.autotest.probe` | `run_probe` / `run_probe_with` / `ProbeOptions` / `extract_summary` | DOM 摘要探针：页面压缩成 KB 级 markdown（CLI `autotest probe`） |
| `baibao.autotest.fixtures` | `browser` / `base_url` / `faker` 等 | opt-in pytest fixture（`pytest_plugins` 启用） |
| `baibao.autotest.fixtures.conftest_template` | — | 角色级 fixture 参考样板（复制改造，勿 import） |

<br />

## 快速上手

### 1. 写页面对象（继承 BasePage）

```python
from baibao.autotest.page import BasePage

class AssetListPage(BasePage):
    """资产列表页。"""

    def click_add_button(self):
        self.click("button:has-text('新增')")

    def search(self, keyword: str):
        self.fill("input[placeholder='请输入编号']", keyword)
        self.click("button:has-text('搜索')")
        self.wait_ready()
```

### 2. 写接口客户端（继承 ApiBase，复用登录态）

```python
from baibao.autotest.api import ApiBase

class AssetApi(ApiBase):
    def add_asset(self, **payload) -> dict:
        return self._parse_json(self._post("/oa/asset/record/add", payload))

    def delete_asset(self, *ids: int) -> dict:
        return self._parse_json(self._post("/oa/asset/record/delete", {"ids": list(ids)}))
```

### 3. 启用基础 fixture（项目根 conftest.py）

```python
# conftest.py
pytest_plugins = ["baibao.autotest.fixtures"]
```

启用后即可用 `browser` / `base_url` / `faker` / `today_str` / `unique_id` 等 fixture。
角色级 fixture（`storage_state` / `page` / `api_context`）请参考
`baibao.autotest.fixtures.conftest_template`，复制改造到项目 conftest。

### 4. 配置环境变量（.env）

```ini
BASE_URL=http://your-host
ADMIN_USERNAME=admin
ADMIN_PASSWORD=xxx
CAPTCHA_VALUE=1234
HEADLESS=false
SLOW_MO=0
USE_BUILTIN_CHROMIUM=false      # true 走内置 Chromium，false 走本地 Chrome
CHROME_PATH=                    # 可选，显式指定 Chrome 路径
```

<br />

## LoginCfg 字段

`LoginCfg` 把登录流程数据驱动化，默认值匹配若依/RuoYi 风格 hash 路由后台。
非 RuoYi 站点改字段选择器即可，无需改源码。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `login_url_suffix` | `"/#/login"` | 拼接到 base_url 后的登录页路径 |
| `username_selector` | `'input[placeholder="用户名"]'` | 用户名输入框选择器 |
| `password_selector` | `'input[type="password"]'` | 密码输入框选择器 |
| `login_button_selector` | `'button.el-button--primary'` | 登录按钮选择器 |
| `captcha_selectors` | `[...]` | 验证码候选选择器（顺序回退；空列表表示无验证码） |
| `success_url_not_contains` | `"/login"` | 登录成功后 URL 不再包含的片段 |
| `viewport` | `{"width":1920,"height":1080}` | 视口尺寸 |

<br />

## 关键设计：为什么自管理 Playwright

本模块**刻意不使用** `pytest-playwright` 提供的 `context` / `page` fixture，
而是自己管理 Playwright 生命周期（`fixtures.browser` + 角色级 `admin_page`）。

**原因**：Element Plus 的 `el-select` 放在 `el-dialog` 内时，pytest-playwright 托管 context
下 Playwright 的合成 `click()` 事件无法触发 Vue 的 `pointerdown` 处理链，下拉面板展不开。
自管理 context + 真实输入（`core.devtools.real_click`）才能解决。

<br />

## DOM 摘要探针（`autotest probe`）

把一个**已登录**页面压缩成 KB 级 markdown 结构摘要（表单项/表格/按钮/弹窗/
消息/下拉实际选项），供 AI 在不读整页 HTML 的前提下了解页面结构——是"AI 写代码
驱动浏览器、省 token"工作流的关键工具。源码能回答的问题读源码，探针只补**运行时
状态**（下拉实际有哪些选项、表格实际有什么数据、弹窗里有哪些字段）。

```bash
# 基本用法（登录态缓存 .auth/admin.json 有效时直接用；失效且给了账号密码则自动重登）
# Git Bash 下推荐纯路由写法（不带开头 # 或 /），免疫 MSYS 参数路径转换
python -m baibao autotest probe it-asset --role admin

# 展开指定下拉后再提取（拿实际选项；无 el-select 时按文本点按钮）
python -m baibao autotest probe purchase/stock --click-label 供应商

# 超简略模式：只留骨架（表单项 label/类型/必填、列头、按钮），去掉值/首行/分页
python -m baibao autotest probe it-asset --brief

# 自定义提取 JS：返回指定数据的紧凑 JSON（应对大数据量页面，如整列抽取）
python -m baibao autotest probe it-asset --js \
  '(() => [...document.querySelectorAll(".el-table__row")].length)()'
# 含引号/超长的 JS 走文件（传 - 读 stdin）
python -m baibao autotest probe it-asset --js-file extract.js

# 摘要写入文件 + 无头模式（默认有头，用户不强调一律有头）
python -m baibao autotest probe "#/oa/asset" --out summary.md --headless
```

> PowerShell/CMD 下 `"#/oa/asset"` 写法可用；Git Bash 下该写法会被转换成
> `#C:/Program Files/Git/...` 导致 404，探针会直接报错提示改写法。

环境变量（当前目录 `.env` 自动加载）：`BASE_URL` / `ADMIN_USERNAME` /
`ADMIN_PASSWORD` / `CAPTCHA_VALUE`；浏览器选择沿用 `USE_BUILTIN_CHROMIUM` /
`CHROME_PATH`（三态见上文）。探针默认**有头**运行（`--headless` 切无头）。

输出约定：摘要 markdown 走 stdout（`--delim` 可包裹），日志走 stderr；
单页摘要硬上限 6000 字符（超出截断并标注）。

程序内调用：`from baibao.autotest.probe import run_probe, extract_summary`——
前者完整流程（含登录态管理），后者在已打开的 `Page` 上直接提取；
参数较多时推荐参数对象版 `from baibao.autotest.probe import ProbeOptions, run_probe_with`。

<br />

## 排坑要点

### 1. el-select 在弹窗内点不开

**现象**：`el-select` 在 `el-dialog` 内，`locator.click()` / `force=True` / `page.mouse.click()`
都无法展开下拉，或展开了但选不中。

**根因**：Playwright 合成事件不触发 Vue 的 `pointerdown`；overlay 遮挡导致点击落空；
全局选择器命中了搜索栏的同名 wrapper（页面上有 9 个 `.el-select__wrapper`）。

**解法**（已内置在 `BasePage.el_select`）：
- 用 `_scope_form_item` 把选择器限定到 `.el-dialog` 内部
- 用 `_cdp_click`（经 `core.devtools.real_click` 发送浏览器底层真实鼠标事件，
  Chromium 系为 CDP `Input.dispatchMouseEvent`）完整触发
  `mouseMoved → mousePressed → mouseReleased` 链

### 2. pytest-playwright 的 page fixture 不要用

装了 `pytest-playwright` 后它自动注册 `page` / `context` fixture。**不要用它们**，
否则会触发上述事件兼容问题。本包的 `fixtures.browser` + 角色级 `admin_page` 已自管理，
`baibao[autotest]` extra 也因此不再依赖 pytest-playwright（只用它的 CLI 选项时自行安装）。

### 3. 登录态缓存失效

`.auth/<role>.json` 默认 12 小时 TTL，过期自动重新登录。可调 `is_auth_valid(path, max_age_hours=...)`。
缓存文件含 cookie，**勿提交到 git**，加入 `.gitignore`。

<br />

## 运行测试

```bash
# 全量
pytest

# 仅冒烟（P0）
pytest -m smoke

# 调试：可见浏览器 + 慢放
pytest --headed --slowmo 200

# 失败时截图/录屏（pytest-playwright 提供的 CLI 选项，需自行安装 pytest-playwright；
# 注意：装了它也别在用例里用它注册的 page/context fixture，原因见上）
pytest --screenshot=only-on-failure --video=retain-on-failure --tracing=retain-on-failure

# 调试器
PWDEBUG=1 pytest tests/it_asset/test_asset_crud.py
```

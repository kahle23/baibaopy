"""
kbase_init 命令 - 知识库（Knowledge Base）目录脚手架。

支持多种知识库模板（公司模板、个人模板等），通过 -t/--template 参数选择，
默认使用「公司」模板。

扩展方式（新增一种知识库模板）：
    1. 构造一个 KbaseTemplate 实例，传入 name / top_levels / project_templates 等数据；
    2. 调用 register_template(实例) 注册；
    3. 完成。命令侧无需任何改动，立即可用 `-t <模板名>` 调用。
       需要自定义生成逻辑时，可继承 KbaseTemplate 重写方法后再实例化注册。

设计原则（防止"目录改名/挪移"风险）：
    - 顶层只按"稳定的职能域"划分，这些域在企业生命周期内几乎不会消失。
    - 每个项目使用同一套标准化模板，复制即用，永不重构。
    - 文件用"命名规范"承载维度（日期/项目/类型/标题/版本），不靠多层文件夹表达维度。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pykunlun.cli import CliContext, Command
from pykunlun.util import logutil

log = logutil.getLogger(__name__)


# region ======== 通用工具 ========
def _mk(path: Path, desc: str = "", title: str = "") -> None:
    """
    创建目录；若提供说明则写入 README.md 作为导航与占位。

    有 desc 时写 README（标题取 title 或目录名）；无 desc 且目录为空时才放 .gitkeep。
    """
    path.mkdir(parents=True, exist_ok=True)
    if desc:
        readme = path / "README.md"
        if not readme.exists():
            heading = title or path.name
            readme.write_text(f"# {heading}\n\n{desc}\n", encoding="utf-8")
    elif not any(path.iterdir()):
        (path / ".gitkeep").write_text("", encoding="utf-8")


def _resolve_root(args: list[str], idx: int) -> Path | None:
    """
    从 args[idx] 读取目标目录，缺省时使用当前目录。

    目录不存在则报错并返回 None，由调用方决定是否中断。
    """
    root = Path(args[idx]) if len(args) > idx else Path.cwd()
    if not root.is_dir():
        log.error(f"目录不存在: {root}")
        return None
    return root
# endregion


# region ======== 知识库模板基类 ========
@dataclass
class KbaseTemplate:
    """
    知识库模板。

    一个模板描述「一种知识库长什么样」：顶层结构、项目模板、预置样板项目。
    通常直接实例化本类、传入数据即可定义一个模板；需要自定义生成逻辑时，
    可继承本类重写 init_base / new_project 后再实例化。

    字段：
        name:             模板唯一标识（公司/个人/公司v2…），用作 -t 参数。
        description:      模板一句话说明。
        top_levels:       顶层目录：{目录名: 说明}。
        second_levels:    二级目录：{顶层名: [子目录]}。
        project_templates:项目模板：{项目类型: {项目子目录: [孙子目录]}}。
                          每种「项目类型」对应一套项目骨架，由该模板自定义。
        seed_projects:    预置样板项目：[(项目类型, 项目名), ...]，固定用 template_number 编号。
        project_root_name:项目资产目录名（项目都平铺在此目录下）。
        template_number:  样板项目固定编号（默认 99，跳过自动编号）。
        number_digits:    项目编号位数（默认 2，即 01/02…）。
    """

    name: str
    description: str = ""
    top_levels: dict[str, str] = field(default_factory=dict[str, str])
    second_levels: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    project_templates: dict[str, dict[str, list[str]]] = field(default_factory=dict[str, dict[str, list[str]]])
    seed_projects: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    project_root_name: str = "02-项目资产"
    template_number: str = "99"
    number_digits: int = 2

    # ---- 生成逻辑（通用，子类通常无需重写）----
    def init_base(self, root: Path) -> None:
        """
        生成完整骨架（顶层 + 二级 + 所有预置样板项目）。

        若目标目录已有完整顶层结构，则视为已初始化，仅补全缺失项并给出提示。
        """
        already = all((root / top).exists() for top in self.top_levels)
        for top, desc in self.top_levels.items():
            _mk(root / top, desc)
            for sub in self.second_levels.get(top, []):
                _mk(root / top / sub)
        for kind, proj_name in self.seed_projects:
            self.new_project(root, kind, proj_name, silent=True)
        if already:
            log.info(f"• [{self.name}] 目录结构已存在，仅补全缺失项：{root}")
        else:
            log.info(f"✓ [{self.name}] 基础骨架已生成：{root}")

    def new_project(self, root: Path, kind: str, name: str, silent: bool = False) -> bool:
        """
        按 project_templates[kind] 新建一个项目目录。

        Args:
            root: 知识库根目录。
            kind: 项目类型（必须存在于 project_templates），如「自研」「三方」。
            name: 项目名（作为文件夹名后半部分），自动加上编号前缀。
            silent: 静默模式（供 init_base 复用，预置样板项目时不打印）。

        Returns:
            True 新建成功；False 表示类型非法或目录已存在被跳过。
        """
        kind = kind.strip()
        template = self.project_templates.get(kind)
        if template is None:
            log.error(f"未知项目类型「{kind}」，可选：{list(self.project_templates)}")
            return False
        base = root / self.project_root_name

        if name.startswith("模板"):
            num = self.template_number
        else:
            num = self._next_number(base)

        proj = base / f"{num}-{name}"
        if proj.exists() and any(proj.iterdir()):
            if not silent:
                log.warning("%s 已存在且非空，已跳过（不覆盖）", proj)
            return False

        for folder, subs in template.items():
            if subs:
                for sub in subs:
                    _mk(proj / folder / sub)
            else:
                _mk(proj / folder)

        # 项目总览 README：取模板第一个目录作为总览目录
        overview_folder = next(iter(template))
        overview_desc = (
            f"- 模板：{self.name}\n"
            f"- 类型：{kind}\n"
            f"- 说明：在此填写本项目的定位、目标、范围、关键干系人与重要链接。\n"
        )
        overview_title = f"{name}（{kind}）"
        _mk(proj / overview_folder, desc=overview_desc, title=overview_title)

        if not silent:
            log.info(f"✓ [{self.name}] 已创建项目（{kind}）：{proj}")
        return True

    def _next_number(self, base: Path) -> str:
        """扫描项目资产目录，返回下一个未占用编号字符串。"""
        used: set[int] = set()
        if base.exists():
            for entry in base.iterdir():
                if entry.is_dir():
                    parts = entry.name.split("-", 1)
                    if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == self.number_digits:
                        used.add(int(parts[0]))
        upper = int(self.template_number)
        for n in range(1, upper):
            if n not in used:
                return f"{n:0{self.number_digits}d}"
        raise RuntimeError(f"项目编号已耗尽（01-{upper - 1:0{self.number_digits}d} 均被占用），无法继续新建")
# endregion


# region ======== 模板注册表 ========
_TEMPLATES: dict[str, KbaseTemplate] = {}


def register_template(template: KbaseTemplate) -> KbaseTemplate:
    """注册一个模板实例。"""
    if not template.name:
        raise ValueError("模板必须有非空 name 属性")
    if template.name in _TEMPLATES:
        raise ValueError(f"模板名「{template.name}」已被注册")
    _TEMPLATES[template.name] = template
    return template


def get_template(name: str) -> KbaseTemplate | None:
    """按名称取模板实例；不存在返回 None。"""
    return _TEMPLATES.get(name)


def list_template_names() -> list[str]:
    """返回所有已注册模板名。"""
    return list(_TEMPLATES)
# endregion


# region ======== 内置模板：公司知识库 ========
register_template(KbaseTemplate(
    name="公司",
    description="公司级知识库：公司资料、项目资产、运营、团队、知识、工具、归档",
    top_levels={
        "00-首页与导航": "知识库首页：使用说明、目录索引、更新日志。新人从这里开始。",
        "01-公司资料": "公司层面的静态背景资料：介绍、组织架构、工商资质、品牌VI、制度总则。",
        "02-项目资产": "所有系统/项目的家。按「自研项目」「三方系统」分，每个项目一套标准模板。",
        "03-运营支持": "公司/团队日常运转需要的文档：通讯录、邮件模板、业务文档模板、常用流程表单。",
        "04-团队与流程": "团队内部的工作方式与规范：研发规范、代码规范、发布变更、入职指南、会议周报。",
        "05-知识沉淀": "可分享的知识点：技术文章、网摘收藏、踩坑记录、培训材料、读书笔记。",
        "06-资源工具箱": "可复用的资产/工具：常用配置文件、AI技能、二进制运行环境、脚本工具、软件安装包。",
        "07-归档": "已结项项目、历史文档。只进不出，永不删除——需要时就移到这里。",
    },
    second_levels={
        "00-首页与导航": ["如何使用本知识库", "目录索引", "更新日志"],
        "01-公司资料": ["公司介绍", "组织架构", "工商与资质", "品牌与VI", "制度总则"],
        "02-项目资产": [],
        "03-运营支持": ["通讯录", "邮件模板", "业务文档模板", "常用流程与表单"],
        "04-团队与流程": ["研发规范", "代码规范", "发布与变更", "入职指南", "会议与周报"],
        "05-知识沉淀": ["技术文章", "网摘与收藏", "踩坑记录", "培训材料", "读书笔记"],
        "06-资源工具箱": ["常用配置文件", "AI技能", "二进制与运行环境", "脚本工具", "软件安装包"],
        "07-归档": ["已结项项目", "历史文档"],
    },
    project_templates={
        "自研": {
            "00-项目概览": ["项目章程", "里程碑与路线图", "会议纪要"],
            "01-需求": ["PRD", "原型与交互", "需求池"],
            "02-设计": ["架构设计", "数据库设计", "接口设计", "UI设计"],
            "03-前端": ["工程说明", "组件库", "构建与发布"],
            "04-后端": ["工程说明", "模块说明", "接口实现"],
            "05-环境部署": ["开发环境", "测试环境", "生产环境", "部署文档"],
            "06-业务逻辑": ["业务规则", "流程说明", "计算与算法"],
            "07-操作手册": ["用户手册", "运维手册"],
            "08-问答FAQ": [],
            "09-运维监控": ["监控告警", "备份与容灾", "故障处理"],
            "99-归档": [],
        },
        "三方": {
            "00-系统概览": ["README"],
            "01-采购与合同": [],
            "02-环境部署": ["部署文档", "环境说明", "License与授权"],
            "03-配置说明": [],
            "04-操作手册": ["管理员手册", "用户手册"],
            "05-集成对接": ["接口对接", "单点登录", "数据同步"],
            "06-问答FAQ": [],
            "99-归档": [],
        },
    },
    seed_projects=[("自研", "模板自研项目"), ("三方", "模板三方系统")],
))


# endregion


# region ======== 参数解析 ========
def _parse_template(args: list[str]) -> tuple[str | None, list[str]]:
    """
    从 args 中剥离 -t / --template 选项。

    支持：`-t 公司`、`--template 公司`、`--template=公司`。
    返回 (模板名或None, 剩余位置参数)；未指定 -t 时模板名为 None；-t 缺值时报错并返回 (None, ...)。
    """
    template = None
    rest: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-t", "--template"):
            if i + 1 >= len(args):
                log.error(f"{a} 缺少参数（模板名）")
                return None, rest
            template = args[i + 1]
            i += 2
            continue
        if a.startswith("--template="):
            template = a.split("=", 1)[1]
            i += 1
            continue
        rest.append(a)
        i += 1
    return template, rest


class KbaseInitCommand(Command):
    """
    知识库（Knowledge Base）目录脚手架。

    通过 -t/--template 指定知识库模板（必须）。
    """

    @property
    def name(self) -> str:
        return "kbase_init"

    @property
    def description(self) -> str:
        return "生成知识库目录骨架（必须 -t 指定模板）或新增项目目录"

    @property
    def usage(self) -> str:
        return (
            "python -m baibao kbase_init -t <模板> [目录]\n"
            "    生成完整骨架（默认当前目录）\n"
            "python -m baibao kbase_init new -t <模板> <类型> <项目名> [目录]\n"
            "    新增一个项目目录（类型由模板定义，如 自研/三方）\n"
            "python -m baibao kbase_init list\n"
            "    列出可用模板"
        )

    def execute(self, ctx: CliContext) -> Any:
        args = ctx.current_args
        # 1) 解析 -t/--template（未指定时 template_name 为 None）
        template_name, rest = _parse_template(args)

        # 2) 子命令：list —— 列出可用模板（无需指定模板）
        if rest and rest[0] == "list":
            names = list_template_names()
            log.info(f"可用知识库模板：{', '.join(names) if names else '（无）'}")
            return True

        # 3) 其余操作必须指定模板
        if template_name is None:
            log.error("必须指定模板：-t <模板名>（可用 kbase_init list 查看可用模板）")
            self.show_usage()
            return False

        tpl = get_template(template_name)
        if tpl is None:
            log.error(f"未知模板「{template_name}」，可用：{list_template_names()}")
            return False

        # 4) 子命令：new <类型> <项目名> [目录]
        if rest and rest[0] == "new":
            if len(rest) < 3:
                log.error(f"参数不足，用法: kbase_init new -t <模板> <类型> <项目名> [目录]"
                          f"（模板「{tpl.name}」支持类型：{list(tpl.project_templates)}）")
                self.show_usage()
                return False
            kind = rest[1]
            name = rest[2]
            root = _resolve_root(rest, 3)
            if root is None:
                return False
            return tpl.new_project(root, kind, name)

        # 5) 默认：生成基础骨架；kbase_init -t <模板> [目录]
        root = _resolve_root(rest, 0)
        if root is None:
            return False
        tpl.init_base(root)
        return True
# endregion

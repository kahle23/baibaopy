"""
envutil — 环境变量与配置的小工具。

  - :func:`load_dotenv_if_present` — 可选加载 ``.env`` 到进程环境
    （显式路径 > 当前目录 > 逐级向上查找）。fixtures 与 CLI 共用（此前两处各复制了一份相同逻辑）。
  - :func:`normalize_base_url` — 去除 ``base_url`` 末尾斜杠（此前在 api/login_state/fixtures/probe/CLI 共 5 处各自 ``rstrip("/")``）。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["load_dotenv_if_present", "normalize_base_url"]


def _find_env_file(env_file: Path | None) -> Path | None:
    """
    定位 ``.env``：显式路径原样返回（存在性交由 dotenv）；
    未给路径时从当前目录起**逐级向上**找第一个存在的 ``.env``。
    """
    if env_file is not None:
        return env_file
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        probe = candidate / ".env"
        if probe.exists():
            return probe
    return None


def load_dotenv_if_present(env_file: Path | str | None = None) -> None:
    """
    可选加载 ``.env`` 到进程环境。

    查找顺序：显式 ``env_file`` > 当前目录 ``.env`` > 从当前目录逐级向上找
    （与裸 ``load_dotenv()`` 按脚本位置向上查找的语义对齐，修掉"CLI 只认
    cwd、脚本按脚本位置找"的分裂；CLI 以 ``-m`` 方式运行时没有脚本位置，
    以 cwd 为锚点逐级向上是唯一可行语义）。

    python-dotenv 默认不覆盖已存在的环境变量，即 Shell/系统环境变量优先于
    ``.env``；python-dotenv 未安装时静默跳过（直接读 os.environ）。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    target = _find_env_file(Path(env_file) if env_file is not None else None)
    if target is not None:
        load_dotenv(target)


def normalize_base_url(base_url: str) -> str:
    """
    去除 ``base_url`` 末尾斜杠（拼接路由/登录地址前的统一规范化）。
    """
    return base_url.rstrip("/")

"""
AI Agent 能力模块（baibao 侧实现）。

承载自治 agent 的组成件，与 :mod:`pykunlun.ai_agent`（抽象层）对应：
本包提供基于 baibao 基础设施的具体实现与模块级实例。

当前包含：
  - 记忆（:mod:`baibao.ai_agent.memory`：基于 rdb_mgr 的 ``RdbMemoryStore``）

后续可扩展技能管理等。原长任务子包已随「计划任务」通用化迁移至
:mod:`baibao.task.plan`（抽象层 :mod:`pykunlun.task.plan`）。
"""

from . import memory
from .memory import RdbMemoryStore, memory_mgr

__all__ = ['RdbMemoryStore', 'memory', 'memory_mgr']

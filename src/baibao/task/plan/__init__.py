"""
计划任务服务模块，提供 MySQL 策略实现。

采用策略模式设计：抽象基类 :class:`PlanTaskService` / 管理器 :class:`PlanTaskManager`
收敛在 :mod:`pykunlun.task.plan`；本包提供基于 baibao ``rdb_mgr`` 的
MySQL 具体实现与模块级默认实例 ``task_mgr``。

模块组织（对标 :mod:`baibao.ai.ocr` 的包拆分）：

  - :mod:`baibao.task.plan.schema`          — ``ai_task_*`` 五张表的列定义
    （单一信息源）与 DDL 生成
  - :mod:`baibao.task.plan.mysql_service`   — :class:`MySqlPlanTaskService` 与
    模块级默认管理器 ``task_mgr``（未来 HTTP 后端实现另立 ``http_service.py`` 即可）

典型用法::

    from baibao.task.plan import MySqlPlanTaskService, task_mgr

    svc = MySqlPlanTaskService(db_name='agent_task')
    svc.setup()                       # 幂等建表
    pkg = svc.claim_next_step(1)      # 原子认领，返回续跑上下文包
"""

from .mysql_service import MySqlPlanTaskService, task_mgr

__all__ = ['MySqlPlanTaskService', 'task_mgr']

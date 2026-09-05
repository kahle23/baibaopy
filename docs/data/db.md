# 数据库模块

> 本文档介绍 `baibao.db` 模块的使用方法，提供统一的关系型数据库操作接口。

## 模块概述

`baibao.db` 模块基于 `pykunlun` 的 `RdbClient` / `RdbManager` 抽象层，提供简洁的关系型数据库操作接口，支持 MySQL、PostgreSQL、SQLite。主要特点：

- 统一的 API 接口，简化数据库操作
- 自动管理连接生命周期（连接池模式：获取、使用、归还）
- 支持多数据源（按别名注册多个客户端实例）
- 参数化查询防止 SQL 注入
- 自动处理事务提交和回滚

核心组件：

| 组件 | 说明 |
| --- | --- |
| `RdbCfg` | 数据库连接配置（host、port、账号、db_type 等） |
| `RdbClient` | 数据库客户端抽象基类；具体实现有 `MysqlClient`、`PostgresqlClient`、`SqliteRdbClient` |
| `rdb` | 模块级 `RdbManager` 默认实例，按别名管理多个客户端 |

## 基本使用

### 导入模块

从 `baibao.db` 导入 `rdb` 与 `RdbCfg`（顶层包不再 re-export 子模块符号）：

```python
from baibao.db import rdb, RdbCfg
```

具体的数据库客户端类需从 `baibao.db.rdb` 导入：

```python
from baibao.db.rdb import MysqlClient, PostgresqlClient, SqliteRdbClient
```

### 数据库配置

使用 `RdbCfg` 创建数据库连接配置：

```python
from baibao.db import RdbCfg
from pykunlun.util import loadutil

# 直接构造配置
cfg = RdbCfg(
    host="localhost",
    port=3306,
    username="root",
    password="your_password",
    database="your_database",
    db_type="mysql",      # 支持 "mysql"、"postgresql"、"sqlite"，默认 "mysql"
    charset="utf8mb4"     # 默认 utf8mb4
)

# 从 JSON 文件加载配置
cfg = loadutil.load_dataclass_from_json_file("db.config", RdbCfg)
```

> SQLite 为文件型数据库，仅需 `database`（文件路径，或 `:memory:` 表示内存库），无需 host/port/username/password。

`db.config` 文件示例：

```json
{
    "host": "localhost",
    "port": 3306,
    "username": "root",
    "password": "your_password",
    "database": "your_database",
    "db_type": "mysql"
}
```

## 数据库客户端管理

客户端需先构造再注册到管理器，随后即可通过管理器按别名执行 SQL。

### 注册客户端

```python
from baibao.db import rdb, RdbCfg
from baibao.db.rdb import MysqlClient

cfg = RdbCfg(host="localhost", port=3306, username="root",
             password="123456", database="test_db")

# 构造客户端并注册（MySQL/PostgreSQL 默认使用连接池模式）
rdb.register("my_db", MysqlClient(cfg))
```

可在构造客户端时自定义连接池参数：

```python
client = MysqlClient(
    cfg,
    use_pool=True,       # 是否使用连接池，默认 True
    mincached=1,         # 最小空闲连接数
    maxcached=10,        # 最大空闲连接数
    maxconnections=20,   # 最大总连接数
)
rdb.register("my_db", client)
```

#### 连接模式说明

**连接池模式（默认）**：适合高并发场景，线程安全
- 基于 DBUtils.PooledDB 实现
- 连接使用后归还到连接池，不会真正关闭
- 可配置最小/最大空闲连接数和最大总连接数

**单连接模式**：适合低并发或资源受限场景，非线程安全，仅限单线程使用
- 使用单个数据库连接
- 返回代理对象，防止调用方误关闭共享连接
- 连接的生命周期由客户端统一管理

```python
# 使用单连接模式
client = MysqlClient(cfg, use_pool=False)
rdb.register("my_db", client)
```

> SQLite 客户端（`SqliteRdbClient`）基于 `pykunlun` 内置实现，采用 connect-per-call（每次执行开关一个连接），无连接池参数。

### 获取客户端

```python
# 获取指定别名的客户端
client = rdb.get_client("my_db")

# 省略别名时使用默认别名 "default"
client = rdb.get_client()
```

### 移除客户端

```python
# 移除指定别名的客户端
rdb.unregister("my_db")

# 移除默认别名的客户端
rdb.unregister()
```

> `unregister()` 仅把客户端从管理器中注销，不会自动关闭其底层连接池；如需释放资源，请对取出的客户端调用 `client.close()`。

### 查看已注册的别名

```python
names = rdb.get_registered_names()   # 例如 ['my_db']
```

## 执行 SQL 操作

注册客户端后，可直接通过 `rdb` 执行 SQL；通过 `name` 指定别名，省略时作用于默认别名 `"default"`。

### 执行写操作（INSERT/UPDATE/DELETE）

使用 `execute()` 方法执行写操作，自动提交事务：

```python
from baibao.db import rdb

# 插入数据
affected_rows = rdb.execute(
    "INSERT INTO users (name, email) VALUES (%s, %s)",
    params=("张三", "zhangsan@example.com"),
    name="my_db",
)
print(f"插入了 {affected_rows} 行")

# 更新数据
affected_rows = rdb.execute(
    "UPDATE users SET email = %s WHERE name = %s",
    params=("new_email@example.com", "张三"),
    name="my_db",
)

# 删除数据
affected_rows = rdb.execute(
    "DELETE FROM users WHERE name = %s",
    params=("张三",),
    name="my_db",
)
```

### 执行查询操作（SELECT）

使用 `query()` 方法执行查询，返回 `list[dict]`：

```python
from baibao.db import rdb

# 查询所有用户
users = rdb.query("SELECT * FROM users", name="my_db")
for user in users:
    print(user)  # {'id': 1, 'name': '张三', 'email': 'zhangsan@example.com'}

# 带参数的查询
users = rdb.query(
    "SELECT * FROM users WHERE name = %s",
    params=("张三",),
    name="my_db",
)

# 将 Decimal 类型转换为 float（适用于金额等字段）
orders = rdb.query(
    "SELECT * FROM orders WHERE amount > %s",
    params=(100,),
    to_float=True,       # Decimal 字段会自动转换为 float
    name="my_db",
)
```

> **占位符**：MySQL/PostgreSQL 使用 `%s`，SQLite 使用 `?`。

## 多数据源管理

支持同时管理多个数据库连接，按别名区分：

```python
from baibao.db import rdb, RdbCfg
from baibao.db.rdb import MysqlClient

# 配置并注册主数据库
main_cfg = RdbCfg(
    host="localhost", port=3306, username="root",
    password="123456", database="main_db",
)
rdb.register("main", MysqlClient(main_cfg))

# 配置并注册日志数据库
log_cfg = RdbCfg(
    host="localhost", port=3306, username="root",
    password="123456", database="log_db",
)
rdb.register("log", MysqlClient(log_cfg))

# 使用指定数据源执行操作
users = rdb.query("SELECT * FROM users", name="main")
logs = rdb.query("SELECT * FROM access_logs", name="log")

# 在指定数据源执行写操作
rdb.execute(
    "INSERT INTO access_logs (user_id, action) VALUES (%s, %s)",
    params=(1, "login"),
    name="log",
)
```

## 完整示例

```python
from baibao.db import rdb, RdbCfg
from baibao.db.rdb import MysqlClient

def main():
    # 1. 配置数据库连接
    cfg = RdbCfg(
        host="localhost",
        port=3306,
        username="root",
        password="your_password",
        database="test_db",
        db_type="mysql",
    )

    # 2. 构造并注册数据库客户端
    rdb.register("test", MysqlClient(cfg))

    try:
        # 3. 创建表
        rdb.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(200) UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, name="test")

        # 4. 插入数据
        affected = rdb.execute(
            "INSERT INTO users (name, email) VALUES (%s, %s)",
            params=("李四", "lisi@example.com"),
            name="test",
        )
        print(f"插入了 {affected} 条记录")

        # 5. 查询数据
        users = rdb.query(
            "SELECT * FROM users WHERE name = %s",
            params=("李四",),
            name="test",
        )
        print(f"查询到 {len(users)} 条记录")

        # 6. 更新数据
        rdb.execute(
            "UPDATE users SET email = %s WHERE name = %s",
            params=("new_email@example.com", "李四"),
            name="test",
        )

        # 7. 删除数据
        rdb.execute(
            "DELETE FROM users WHERE name = %s",
            params=("李四",),
            name="test",
        )

    except Exception as e:
        print(f"操作失败: {e}")

    finally:
        # 8. 释放连接池资源并移除客户端
        client = rdb.get_client("test")
        client.close()
        rdb.unregister("test")

if __name__ == "__main__":
    main()
```

## 注意事项

1. **参数化查询**：始终使用占位符（MySQL/PostgreSQL 为 `%s`，SQLite 为 `?`）配合 `params` 参数，避免 SQL 注入
2. **连接管理**：`execute()` 和 `query()` 方法会自动管理连接生命周期，无需手动关闭
3. **事务处理**：`execute()` 方法自动提交事务，异常时自动回滚
4. **配置文件**：可通过 `loadutil.load_dataclass_from_json_file(path, RdbCfg)` 从 JSON 文件加载配置
5. **字符集**：MySQL 默认使用 `utf8mb4` 字符集，支持完整的 Unicode 字符

## 错误处理

```python
from baibao.db import rdb

try:
    # 尝试执行数据库操作
    result = rdb.query("SELECT * FROM non_existent_table")
except ValueError as e:
    # 别名尚未注册时抛出 ValueError
    print(f"配置错误: {e}")
except Exception as e:
    print(f"数据库操作失败: {e}")
```

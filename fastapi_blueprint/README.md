# FastAPI 正式化项目骨架

这个目录不是当前 V1.7 的运行入口，而是后续把单文件 MVP 拆成正式平台时的推荐结构。

## 推荐结构

```text
fastapi_blueprint/
  app/
    main.py                 # FastAPI 入口
    api/                    # REST API 路由
    core/config.py          # 环境变量与系统配置
    db/session.py           # PostgreSQL 连接
    models/                 # SQLAlchemy ORM 模型
    services/collector.py   # CSV/Excel/设备数据采集服务
    workers/collector_worker.py
  requirements.txt
```

## 迁移顺序建议

1. 先把 V1.7 的 SQLite 表结构迁移成 PostgreSQL 表。
2. 把 CSV/Excel 读取逻辑抽到 `services/collector.py`。
3. 把定时采集从 Web 进程拆到 `workers/collector_worker.py`。
4. Web 端只负责配置、查询、导出和权限。
5. 接入 AD/LDAP 或公司统一登录。
6. 加 HTTPS、反向代理、备份、审计和配置审批。
7. 再考虑 MES 回写与 Store-and-Forward 队列。

## 为什么要拆

单文件版本适合验证流程和现场需求，但正式平台需要把“页面服务”和“采集服务”隔离。共享目录、网络波动、设备文件锁、MES 接口超时都不应该卡住用户页面。

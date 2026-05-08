# Models 模块规划

建议从 V1.7 的 SQLite 表迁移为 SQLAlchemy 模型：

- `User`
- `ProductionConfig`
- `MeasurementItemConfig`
- `MetricConfig`
- `MeasurementResult`
- `CollectLog`
- `AuditLog`

正式版建议把配置变更历史独立成表，例如 `ConfigRevision`，用于审批、diff、回滚和追溯。

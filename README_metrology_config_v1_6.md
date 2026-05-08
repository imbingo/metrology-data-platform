# 量测数据采集配置平台 V1.6 - 工业化增强版

本版针对 V1.5 的工程风险做了补强，仍是单文件原型，但更适合在内网电脑/服务器上试运行。

## 主要增强

1. 文件冲突处理
   - 读取前检查文件 size/mtime 是否稳定
   - 读取后再次检查文件是否变化
   - 遇到 PermissionError / OSError / 文件变化会自动重试
   - 先读取成内存字节快照，再解析 CSV，减少占用源文件时间

2. 网络波动与 UI 卡死缓解
   - 前台“测试读取/立即采集”增加读取超时保护
   - 默认超过 20 秒返回 READ_TIMEOUT，不让页面一直等
   - 后台定时采集后续周期会继续重试

3. 数据一致性
   - SQLite 开启 WAL、busy_timeout、foreign_keys
   - measurement_result 使用唯一 source_metric_hash 去重
   - CSV 表头/生产编号/指标字段缺失会记录日志，而不是静默失败

4. 安全与审计
   - 管理员账号密码支持环境变量覆盖：MDCP_ADMIN_USERNAME / MDCP_ADMIN_PASSWORD
   - 新增 audit_log 表与“审计日志”页面
   - 记录登录成功/失败、退出、生产编号保存、量测项保存、指标保存、配置导入/导出

## 运行

```powershell
cd "D:\project\量测\数据收集平台"
python .\metrology_config_app_v1_6_port8016.py
```

打开：

```text
http://127.0.0.1:8016
```

验证版本：

```text
http://127.0.0.1:8016/version
```

默认账号：admin  
默认密码：admin123

## 正式试运行建议

建议不要使用默认密码。PowerShell 中可以这样设置：

```powershell
$env:MDCP_ADMIN_USERNAME="admin"
$env:MDCP_ADMIN_PASSWORD="你的强密码"
python .\metrology_config_app_v1_6_port8016.py
```

读取参数可调：

```powershell
$env:MDCP_READ_TIMEOUT_SECONDS="20"
$env:MDCP_READ_RETRY_COUNT="3"
$env:MDCP_READ_RETRY_INTERVAL_SECONDS="1.0"
$env:MDCP_FILE_STABLE_WAIT_SECONDS="0.4"
```

## 仍然不是 Ignition/MES 级正式平台

V1.6 已经增强了文件读取和审计，但仍然是单文件 MVP。若要变成真正工业级平台，下一步建议拆分为：

- FastAPI 后端服务
- PostgreSQL 数据库
- 独立 Collector Worker
- Redis/Celery 或消息队列
- AD/LDAP 登录
- HTTPS + 反向代理
- 配置变更审批与备份
- MES/API 回写时的 Store-and-Forward 队列

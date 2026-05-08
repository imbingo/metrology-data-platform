# 量测数据采集配置平台

这是一个用于内网试运行的半导体量测数据采集配置平台原型。当前主版本是 V1.7，支持生产编号、量测项、指标配置、CSV/Excel 数据源读取、采集结果查询、日志审计、用户角色权限和结果导出。

## 推荐运行版本

```powershell
cd "D:\量测数据收集平台"
python .\metrology_config_app_v1_7_port8017.py
```

打开：

```text
http://127.0.0.1:8017
```

默认账号：admin  
默认密码：admin123

正式试运行建议使用环境变量覆盖默认账号密码：

```powershell
$env:MDCP_ADMIN_USERNAME="admin"
$env:MDCP_ADMIN_PASSWORD="你的强密码"
python .\metrology_config_app_v1_7_port8017.py
```

也可以直接运行：

- `start_metrology_v1_7.bat`
- `start_metrology_v1_7.ps1`

## 主要文件

- `metrology_config_app_v1_7_port8017.py`：当前主程序
- `README_metrology_config_v1_7.md`：V1.7 说明文档
- `metrology_config_app_v1_6_port8016.py`：上一版保留备份
- `fastapi_blueprint/`：后续正式化为 FastAPI + PostgreSQL + Collector Worker 的骨架

## 注意

`*.db` 是本地运行生成的数据文件，不提交到仓库。正式部署前建议迁移到 PostgreSQL，并将采集任务从 Web 进程中拆分为独立 Worker。

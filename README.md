# Metrology Data Platform

量测数据收集配置平台，用于在内网环境管理生产编号、量测项目、指标配置、CSV/Excel 数据源采集、结果查询和导出。

## 当前推荐版本

最新版入口文件：

```powershell
python .\metrology_config_app_v2_3_pie_delete_process_guard.py
```

默认访问地址：

```text
http://127.0.0.1:8023
```

默认账号：

```text
admin / admin123
```

该账号仅用于本地测试。正式部署前必须改为环境变量或数据库中的独立管理员账号。

可通过环境变量覆盖默认配置：

```powershell
$env:MDCP_HOST="127.0.0.1"
$env:MDCP_PORT="8023"
$env:MDCP_DISPLAY_IP="10.21.210.75"
python .\metrology_config_app_v2_3_pie_delete_process_guard.py
```

## 主要文件

- `metrology_config_app_v2_3_pie_delete_process_guard.py`: V2.3 最新单文件应用，来自本机 Downloads 中的最新版。
- `metrology_config_app_v2_0_xlsx_template_wizard_10_21_210_75.py`: V2.0 Excel 多 Sheet 模板向导版本。
- `metrology_config_app_v1_7_port8017.py`: V1.7 历史主程序。
- `metrology_config_app_v1_6_port8016.py`: V1.6 历史备份。
- `fastapi_blueprint/`: 后续迁移到 FastAPI + PostgreSQL + Worker 架构的蓝图。

## 本地运行数据

运行时会生成本地数据库、缓存目录和临时文件，这些文件不提交到仓库。正式部署前建议迁移到 PostgreSQL，并把采集任务从 Web 进程中拆分为独立 worker。

本地导出的 Excel 结果和测试工作簿也不提交到公开仓库；如需共享真实量测数据，请使用私有仓库、Release 附件或单独的数据存储位置。

# 量测数据采集配置平台 V1.7 - 扩展增强版

V1.7 基于 V1.6 继续增强，仍然保持“单文件可运行”的 MVP 形态，方便先在内网电脑或小服务器上试运行。

## 新增能力

1. CSV / Excel 数据源
   - CSV 继续支持编码自动识别、文件稳定性检查和读取重试。
   - 新增 `.xlsx/.xlsm` 读取，支持指定 Sheet 名称。
   - Excel 留空 Sheet 时默认读取第一个 Sheet。
   - `.xls` 属于旧二进制格式，建议另存为 `.xlsx`。

2. 配置复制、删除、停用
   - 生产编号配置可复制和删除。
   - 量测项配置可复制和删除。
   - 指标配置可删除。
   - 删除配置不会主动删除历史采集结果，避免破坏追溯数据。

3. 页面优化
   - 内部工具风格更紧凑。
   - 操作区、表格、状态标签和移动端布局做了整理。

4. 用户与角色权限
   - 管理员：admin，可管理用户、配置和采集。
   - 量测工程师：engineer，可维护配置并执行采集。
   - 只读查看：viewer，只能查看 Dashboard、采集结果和日志。

5. 采集结果导出 Excel
   - 采集结果页面支持按筛选条件导出 `.xlsx`。
   - 导出最多取最近 5000 条匹配结果。

6. 一键启动
   - 新增 `start_metrology_v1_7.bat`
   - 新增 `start_metrology_v1_7.ps1`

7. 正式化项目骨架
   - 新增 `fastapi_blueprint/`，用于后续拆成 FastAPI + PostgreSQL + Collector Worker。

## 运行

```powershell
cd "D:\量测数据收集平台"
python .\metrology_config_app_v1_7_port8017.py
```

打开：

```text
http://127.0.0.1:8017
```

验证版本：

```text
http://127.0.0.1:8017/version
```

默认账号：admin  
默认密码：admin123

## 正式试运行建议

建议不要使用默认密码。PowerShell 中可以这样设置：

```powershell
$env:MDCP_ADMIN_USERNAME="admin"
$env:MDCP_ADMIN_PASSWORD="你的强密码"
python .\metrology_config_app_v1_7_port8017.py
```

读取参数仍然可调：

```powershell
$env:MDCP_READ_TIMEOUT_SECONDS="20"
$env:MDCP_READ_RETRY_COUNT="3"
$env:MDCP_READ_RETRY_INTERVAL_SECONDS="1.0"
$env:MDCP_FILE_STABLE_WAIT_SECONDS="0.4"
```

## Excel 配置示例

量测项配置：

```text
数据源类型：Excel xlsx/xlsm
数据源路径：\\192.168.1.100\share\result.xlsx
Excel Sheet 名称：CD量测
生产编号字段名：生产编号
```

指标配置：

```text
Dx1,Dy1,Dx2,Dy2,Rz
```

Excel 第一行需要是表头，例如：

```text
生产编号 | Dx1 | Dy1 | Dx2 | Dy2 | Rz
TEST001 | 0.12 | 0.15 | 0.11 | 0.16 | 3.25
```

## 重要边界

V1.7 仍是单文件 MVP，不是最终工业级平台。正式上线建议继续推进：

- FastAPI 后端服务
- PostgreSQL 数据库
- 独立 Collector Worker
- Redis/Celery 或消息队列
- AD/LDAP 登录
- HTTPS + 反向代理
- 配置变更审批与备份
- MES/API 回写时的 Store-and-Forward 队列

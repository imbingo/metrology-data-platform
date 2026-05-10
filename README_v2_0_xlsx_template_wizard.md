# 量测数据采集配置平台 V2.0 - Excel 多 Sheet 模板向导版

## 主要更新

相比 V1.9，本版支持：

- 模板文件支持 `.xlsx` / `.xlsm`
- Excel 模板文件可包含多个 Sheet
- 上传 Excel 后，如果不填写 Sheet 名称，系统会先显示 Sheet 列表让你选择
- 选择 Sheet 后读取该 Sheet 表头
- 可选择“生产编号字段”
- 可勾选多个量测指标字段
- 保存为模板后，可在生产编号下“一键套用模板”
- 套用模板后会自动生成量测项和指标配置
- 实时数据源也支持 Excel 指定 Sheet 采集
- 原 CSV 模板功能保留

## 运行方式

把文件放到：

```text
D:\project\量测\数据收集平台
```

运行：

```powershell
cd "D:\project\量测\数据收集平台"
python .\metrology_config_app_v2_0_xlsx_template_wizard_10_21_210_75.py
```

本机访问：

```text
http://127.0.0.1:8020
```

局域网其他电脑访问：

```text
http://10.21.210.75:8020
```

如果其他电脑打不开，在服务器电脑上用管理员 PowerShell 放行端口：

```powershell
New-NetFirewallRule -DisplayName "MDCP Web 8020" -Direction Inbound -Protocol TCP -LocalPort 8020 -Action Allow
```

## 推荐使用流程

### 1. 进入模板库

打开：

```text
模板库 → 创建字段映射模板
```

### 2. 上传 Excel 模板

选择你的 `.xlsx` 文件。

如果你没有填写 Sheet 名称，系统会先进入 Sheet 选择页面。

### 3. 选择 Sheet

例如：

```text
量测结果
CD_Result
Overlay_Result
```

选择后系统会读取该 Sheet 的表头。

### 4. 选择字段

选择：

```text
生产编号字段：生产编号
量测指标字段：Dx1, Dy1, Dx2, Dy2, Rz
```

然后保存模板。

### 5. 套用模板

进入某个生产编号：

```text
生产编号 → 量测项配置 → 从模板新增量测项
```

选择刚保存的模板，填写实时 Excel 路径，例如：

```text
\\192.168.1.100\share\result.xlsx
```

保存后，系统会自动生成量测项和指标。

### 6. 测试读取 / 立即采集

进入量测项列表，点击：

```text
测试读取
```

确认能读到数据后，再点击：

```text
立即采集
```

## Excel 格式要求

每个 Sheet 推荐是这种结构：

| 生产编号 | Dx1 | Dy1 | Dx2 | Dy2 | Rz |
|---|---:|---:|---:|---:|---:|
| TEST001 | 0.12 | 0.15 | 0.11 | 0.16 | 3.25 |
| TEST002 | 0.10 | 0.13 | 0.12 | 0.14 | 3.18 |

注意：

- 当前版本支持 `.xlsx` / `.xlsm`
- 不支持旧版 `.xls`，请先另存为 `.xlsx`
- Excel 里要有表头行
- 表头所在行默认是第 1 行，也可以在模板创建页面修改
- 实时采集时会按“生产编号”字段匹配对应行

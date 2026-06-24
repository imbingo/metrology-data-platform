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

- `metrology_config_app_v2_3_pie_delete_process_guard.py`: V2.4 最新单文件应用，支持 CSV/Excel/Image OCR 数据源。
- `metrology_config_app_v2_0_xlsx_template_wizard_10_21_210_75.py`: V2.0 Excel 多 Sheet 模板向导版本。
- `metrology_config_app_v1_7_port8017.py`: V1.7 历史主程序。
- `metrology_config_app_v1_6_port8016.py`: V1.6 历史备份。
- `fastapi_blueprint/`: 后续迁移到 FastAPI + PostgreSQL + Worker 架构的蓝图。

## 图片 OCR 数据源

V2.4 新增 `image` 数据源类型，可从设备导出的 `.png/.jpg/.jpeg/.bmp/.tif/.tiff` 图片中用本地 OCR 抓取 Rx、Ry、Z 等指标。图片路径支持单个文件、目录或 glob；目录模式会选择最新且稳定的图片文件。

安装 OCR Python 依赖：

```powershell
pip install -r .\requirements_ocr.txt
```

Windows 还需要安装 Tesseract-OCR，并在需要时指定可执行文件路径：

```powershell
$env:MDCP_TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
```

若产线电脑不能联网，推荐使用产线 EXE 离线包：

```powershell
.\start_metrology_v2_4_exe.ps1
```

EXE 包不要求产线电脑安装 Python；Python 运行时和 Python OCR 依赖已经打进 `metrology_data_platform_v2_4/` 目录。产线电脑首次运行时只需要安装或定位包内的 Tesseract-OCR。

局域网访问前，可先在服务器电脑上测试 8023 端口是否能被其他电脑访问：

```powershell
.\test_8023_lan_port.ps1 -Mode Server -Port 8023
```

然后在另一台局域网电脑的浏览器中访问脚本显示的 `http://服务器IP:8023`。也可以在另一台电脑运行：

```powershell
.\test_8023_lan_port.ps1 -Mode Client -ServerIp 服务器IP -Port 8023
```

源码离线运行方式仍然保留。复制整个项目目录到产线电脑后，直接运行：

```powershell
.\start_metrology_v2_4_ocr.ps1
```

该脚本会从本地 `offline_ocr_bundle/python_wheels` 安装 Python OCR 依赖，从本地 `offline_ocr_bundle/tesseract_installer` 安装 Tesseract，并启动 V2.4 主程序。离线包需要和产线电脑的 Python 版本/Windows 位数匹配；当前包按 Windows x64 / Python 3.14.6 准备。

量测项选择 `Image OCR` 后，在 `Image OCR config JSON` 中配置 ROI 和正则，例如：

```json
{
  "file_pattern": "*",
  "process_from_filename_regex": "result_(?P<process_step>[^.]+)",
  "ocr": {"lang": "eng", "psm": 6, "scale": 2.0, "threshold": true},
  "metrics": {
    "Rx": {"roi": [0.05, 0.10, 0.30, 0.12], "regex": "Rx\\s*[:=]?\\s*([-+]?\\d+(?:\\.\\d+)?)"},
    "Ry": {"roi": [0.05, 0.24, 0.30, 0.12], "regex": "Ry\\s*[:=]?\\s*([-+]?\\d+(?:\\.\\d+)?)"},
    "Z": {"roi": [0.05, 0.38, 0.30, 0.12], "regex": "Z\\s*[:=]?\\s*([-+]?\\d+(?:\\.\\d+)?)"}
  }
}
```

ROI 使用归一化坐标 `[x, y, w, h]`，范围为 0 到 1。生产编号默认来自当前量测项所属生产编号；工序默认使用固定量测工序，如需从文件名解析，可配置 `process_from_filename_regex`。

## 本地运行数据

运行时会生成本地数据库、缓存目录和临时文件，这些文件不提交到仓库。正式部署前建议迁移到 PostgreSQL，并把采集任务从 Web 进程中拆分为独立 worker。

本地导出的 Excel 结果和测试工作簿也不提交到公开仓库；如需共享真实量测数据，请使用私有仓库、Release 附件或单独的数据存储位置。

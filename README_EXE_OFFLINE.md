# 产线离线 EXE 包使用说明

本包用于没有互联网的 Windows x64 产线电脑。产线电脑不需要预装 Python。

## 包内包含

- `metrology_data_platform_v2_4/`: 已用 PyInstaller 打包好的 V2.4 程序，Python 运行时和 Python OCR 依赖已经包含在此目录。
- `offline_ocr_bundle/tesseract_installer/`: Tesseract-OCR 离线安装程序。
- `start_metrology_v2_4_exe.ps1`: 产线启动脚本，会先安装或定位 Tesseract，再启动平台并打开浏览器。

## 首次运行

在 PowerShell 中进入解压后的目录，运行：

```powershell
.\start_metrology_v2_4_exe.ps1
```

默认会静默安装 Tesseract-OCR。如果公司 IT 要看到安装界面，运行：

```powershell
.\start_metrology_v2_4_exe.ps1 -InteractiveInstall
```

启动后访问：

```text
http://127.0.0.1:8023
```

默认测试账号：

```text
admin / admin123
```

## 已预装 Tesseract 的电脑

如果 Tesseract 已经安装在标准路径，脚本会自动找到它。若安装在自定义路径，先设置：

```powershell
$env:MDCP_TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
.\start_metrology_v2_4_exe.ps1
```

## 端口

如果 8023 被占用，可指定其他端口：

```powershell
.\start_metrology_v2_4_exe.ps1 -Port 8030
```

## 重要说明

- Python 不需要在产线电脑上安装。
- Tesseract 是本地 OCR 引擎，图片识别不会上传到云端。
- Tesseract-OCR 是开源免费软件；生产环境仍建议由公司 IT 按内部软件合规流程确认后分发。
- 整个目录要保持完整，不要只复制单个 `.exe` 文件；PyInstaller 的 onedir 模式需要旁边的 `_internal` 运行时目录。

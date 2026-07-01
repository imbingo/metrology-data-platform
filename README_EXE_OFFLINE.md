# 产线离线 EXE 包使用说明

本包用于没有互联网的 Windows x64 产线电脑。产线电脑不需要预装 Python。

## 包内包含

- `metrology_data_platform_v2_4/`: 已用 PyInstaller 打包好的 V2.4 程序，Python 运行时和 Python OCR 依赖已经包含在此目录。
- `offline_ocr_bundle/tesseract_installer/`: Tesseract-OCR 离线安装程序。
- `metrology_login_launcher.exe`: 日常登录入口，双击后启动后台服务并打开登录页。
- `start_metrology_v2_4_exe.ps1`: 产线启动脚本，会先安装或定位 Tesseract，再启动平台并打开浏览器。
- `test_8023_lan_port.ps1`: 局域网 8023 端口连通性测试脚本，不依赖平台主程序。

## 日常登录

首次部署完成后，日常使用直接双击：

```text
metrology_login_launcher.exe
```

启动器提供两种模式：

- `仅本机使用`: 只允许服务器电脑本机访问。
- `局域网服务器模式`: 允许局域网其他电脑访问，登录地址显示为 `http://服务器IP:8023`。

点击 `启动并打开登录页` 后，会自动启动后台服务并打开登录页。

## 首次运行/安装 OCR

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

## 局域网访问测试

正式启动平台前，可以先用测试脚本确认公司电脑是否允许访问 8023 端口。

在服务器电脑上运行：

```powershell
.\test_8023_lan_port.ps1 -Mode Server -Port 8023
```

脚本会显示本机可用的访问地址，例如：

```text
http://192.168.1.20:8023
```

在另一台局域网电脑上打开浏览器访问该地址；如果看到 `MDCP 8023 LAN PORT TEST OK`，说明端口连通。也可以把本包复制到另一台电脑后运行：

```powershell
.\test_8023_lan_port.ps1 -Mode Client -ServerIp 192.168.1.20 -Port 8023
```

如果连不上，需要让 IT 在服务器电脑上按公司安全策略放行 TCP 8023 入站访问，建议只放行指定内网网段或指定电脑 IP。

## 重要说明

- Python 不需要在产线电脑上安装。
- Tesseract 是本地 OCR 引擎，图片识别不会上传到云端。
- Tesseract-OCR 是开源免费软件；生产环境仍建议由公司 IT 按内部软件合规流程确认后分发。
- 整个目录要保持完整，不要只复制单个 `.exe` 文件；PyInstaller 的 onedir 模式需要旁边的 `_internal` 运行时目录。

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "============================================================"
Write-Host "量测数据采集配置平台 V1.7"
Write-Host "URL: http://127.0.0.1:8017"
Write-Host ""
Write-Host "正式试运行建议先设置环境变量："
Write-Host '$env:MDCP_ADMIN_USERNAME="admin"'
Write-Host '$env:MDCP_ADMIN_PASSWORD="你的强密码"'
Write-Host "============================================================"

python "$PSScriptRoot\metrology_config_app_v1_7_port8017.py"

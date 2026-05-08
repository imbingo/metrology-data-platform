@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo 量测数据采集配置平台 V1.7
echo URL: http://127.0.0.1:8017
echo.
echo 如需正式试运行，请先设置：
echo   set MDCP_ADMIN_USERNAME=admin
echo   set MDCP_ADMIN_PASSWORD=你的强密码
echo ============================================================
python "%~dp0metrology_config_app_v1_7_port8017.py"
if errorlevel 1 (
  echo.
  echo python 启动失败。请确认已经安装 Python，并且可以在命令行执行 python。
)
pause

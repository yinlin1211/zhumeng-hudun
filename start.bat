@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"

echo ========================================
echo   农民工维权网站 + AI智能助手
echo   启动后浏览器打开 http://localhost:8000
echo   按 Ctrl+C 停止
echo ========================================
echo.

REM ===== 第1步：检测 Python =====
set "PYTHON="
where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON (
    where py >nul 2>nul && set "PYTHON=py -3"
)
if not defined PYTHON (
    where python3 >nul 2>nul && set "PYTHON=python3"
)
if not defined PYTHON (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 以上版本
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH"（添加到环境变量）
    echo.
    pause
    exit /b 1
)
echo [检测] Python: %PYTHON%

REM ===== 第2步：首次运行自动创建环境 =====
if not exist "venv\Scripts\python.exe" (
    echo.
    echo [首次运行] 正在创建运行环境（约1-2分钟，仅首次需要）...
    %PYTHON% -m venv venv
    if errorlevel 1 (
        echo [错误] 创建环境失败，请检查 Python 是否正常安装
        pause
        exit /b 1
    )
    echo [首次运行] 正在安装依赖包...
    venv\Scripts\python.exe -m pip install --upgrade pip -q
    venv\Scripts\pip install -r requirements.txt -q
    if errorlevel 1 (
        echo [错误] 安装依赖失败，请检查网络连接
        pause
        exit /b 1
    )
    echo [环境配置完成]
)

REM ===== 第3步：配置大模型服务（密钥见项目根目录 keys.env，由 app/config.py 读取）=====
set LLM_BASE_URL=https://api.deepseek.com/v1
set LLM_MODEL=deepseek-flash

REM ===== 第3.5步：配置讯飞方言大模型密钥（方言识别）=====
REM 方言大模型自动识别 202 种方言，accent=mulacc 无需指定语种；密钥同见 keys.env

REM ===== 第4步：启动服务 =====
echo.
echo 正在启动服务，请稍候...
echo 启动成功后浏览器打开 http://localhost:8000
echo.
venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause

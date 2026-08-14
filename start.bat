@echo off
echo 初始化中
color 7
setlocal enabledelayedexpansion
chcp 936 >nul 2>&1
set er=0
echo 检查启动参数
if "%~1"=="" (
    echo 未检测到文件路径
) else (
copy /Y "%~1" "%~dp0resources\app\index.epub" >nul &&echo 复制成功 || echo 复制失败，文件不存在或权限不足
)
cd /d "%~dp0"
echo 检查端口占用中
netstat -aon | findstr "30971"| findstr "LISTENING" >nul&&set er=1&&echo 检查到端口占用&&echo 按下[1]打开主程序&&echo 按下[2]关闭此窗口&&choice /c 12 >nul


if "%er%" == "1" (
if "%errorlevel%" == "1" (
echo 正在启动主程序
start "" ".\index.exe" >nul
echo 已启动
exit /b
)
if "%errorlevel%" == "2" (exit /b)

) else (
echo 未发现端口占用
echo 正在启动朗读服务
start /min "" ".\.SayService.exe" >nul&&set a=1||set a=2
set num=0
if "%a%" == "2" (
color 4
echo 未找到朗读服务程序
echo 请检查朗读服务是否被重命名或删除
timeout 1
exit /b
) else (
echo 已启动朗读服务
set ok=0
:for
netstat -aon | findstr "30971"| findstr "LISTENING" >nul
set ok=%errorlevel%
if "%ok%" == "0" (
start "" ".\index.exe" >nul||echo 主程序不存在或权限不足
echo 已启动
exit /b
) else (
echo 等待端口启用
timeout 1 >nul
set /a num=%num%+1 >nul
)
if "%num%" == "60" (
echo 长时间未启动将退出
taskkill /f /im SayService.exe >nul
echo 按任意键退出
timeout -1 >nul
exit /b
)
goto for
)
)
exit /b

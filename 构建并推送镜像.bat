@echo off
chcp 65001 >nul
title 打包并推送 Docker 镜像 (Sealos 部署专用)
echo ========================================================
echo  🚀 A-Yong Solar Video WebApp · 打包镜像并推送到 Docker Hub
echo ========================================================
cd /d "%~dp0"

set /p TAG="请输入要构建推送的镜像版本 Tag (直接回车默认 v1): "
if "%TAG%"=="" set TAG=v1

echo.
echo [1/2] 正在构建 Docker 镜像: sczl/my-video:%TAG% ...
docker build -t sczl/my-video:%TAG% .
if %errorlevel% neq 0 (
    echo.
    echo [错误] 镜像构建失败，请检查 Docker Desktop 是否正常启动！
    pause
    exit /b %errorlevel%
)

echo.
echo [2/2] 正在推送到 Docker Hub: sczl/my-video:%TAG% ...
docker push sczl/my-video:%TAG%
if %errorlevel% neq 0 (
    echo.
    echo [错误] 镜像推送失败！如果您尚未登录，请先在终端运行: docker login
    pause
    exit /b %errorlevel%
)

echo.
echo ========================================================
echo  🎉 镜像 sczl/my-video:%TAG% 已成功推送到 Docker Hub！
echo  下一步：前往 Sealos 控制台更新应用镜像即可在线秒级上线！
echo ========================================================
pause

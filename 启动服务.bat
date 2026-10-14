@echo off
chcp 65001 >nul
title A-Yong Solar Video WebApp 随身生产线后端
echo ========================================================
echo  🚀 A-Yong Solar Video WebApp · 川渝光伏短视频中枢启动中
echo ========================================================
cd /d "%~dp0"
python main.py
pause

@echo off
title TikTok Product Hunter — Kamran Ashraf (Kami)
color 0B
cd /d "%~dp0"
echo ========================================================================
echo   TikTok Shop Product Hunter v4 -- Crafted by Kamran Ashraf (Kami)
echo ========================================================================
echo.
python tiktok_product_hunter.py
if errorlevel 1 (
    echo.
    echo ========================================================================
    echo   Process stopped or encountered an error. Press any key to close.
    echo ========================================================================
    pause >nul
)

@echo off
rem Double-click to stop Kash, including one running hidden in the background.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-kash.ps1"
powershell -NoProfile -Command "Start-Sleep 2"

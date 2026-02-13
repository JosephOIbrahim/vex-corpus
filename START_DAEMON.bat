@echo off
title VEX Corpus Daemon
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File ".\start.ps1" -Mode run
pause

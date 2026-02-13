@echo off
title VEX Corpus Tests
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File ".\start.ps1" -Mode test
pause

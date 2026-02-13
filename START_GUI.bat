@echo off
title VEX Corpus GUI
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File ".\start.ps1" -Mode gui
pause

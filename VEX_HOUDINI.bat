@echo off
title VEX Corpus - Houdini Mode
cd /d "%~dp0"

echo.
echo =============================================================
echo   VEX CORPUS - HOUDINI INTEGRATION (hrpyc)
echo =============================================================
echo.
echo Default port: 18811 (hrpyc standard)
echo.
echo Make sure Houdini is running with hrpyc server enabled:
echo.
echo   In Houdini Python Shell:
echo     import hrpyc
echo     hrpyc.start_server()
echo.
echo   Or paste the contents of: houdini\enable_port.py
echo.
echo =============================================================
echo.

python vex.py --houdini %*

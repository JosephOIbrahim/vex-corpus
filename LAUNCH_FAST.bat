@echo off
title VEX Corpus - Fast Mode (Tier 1 Only)
cd /d "%~dp0"
python launch.py --no-tier2 %*
pause

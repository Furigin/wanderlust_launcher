@echo off
rem Only ASCII here on purpose: a .bat is read in the console codepage, and
rem Cyrillic inside it turns into garbage. All human-facing text is printed
rem by mods.py itself.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\rusla\Desktop\wanderlust_launcher"
python scripts\mods.py %*
echo.
pause

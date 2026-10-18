@echo off
cd /d "%~dp0"
py -3 -m savethesnap ui
if errorlevel 1 pause

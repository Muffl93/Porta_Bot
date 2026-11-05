@echo off
cd /d "%~dp0"
python checker.py >> lauf.log 2>&1

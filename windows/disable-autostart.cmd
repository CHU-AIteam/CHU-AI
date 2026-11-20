@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Action DisableAutoStart -Worker
if errorlevel 1 pause

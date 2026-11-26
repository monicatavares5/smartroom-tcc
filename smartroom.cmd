@echo off
rem Atalho para smartroom.ps1 (funciona com duplo clique ou no CMD). Repassa os parametros.
rem Usa o PowerShell 7 (pwsh) se estiver instalado; senao, o Windows PowerShell.
set "PSMODULEPATH="
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0smartroom.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0smartroom.ps1" %*
)
if errorlevel 1 pause

@echo off
setlocal EnableExtensions
set "BEATSOLVER_ROOT=%~dp0.."
set "APP_PATH=%~dp0"
if not exist "%BEATSOLVER_ROOT%\qa\RUN_LOCAL_QA_v052.ps1" (
    echo Missing v0.5.2 QA runner. Extract the qa folder alongside BeatSolver_Timeline_v0.5.2.
    exit /b 2
)
if not exist "%BEATSOLVER_ROOT%\reports" mkdir "%BEATSOLVER_ROOT%\reports"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTEST_ADDOPTS=-p no:cacheprovider"
echo Running local Windows QA for BeatSolver v0.5.2.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%BEATSOLVER_ROOT%\qa\RUN_LOCAL_QA_v052.ps1" -AppPath "%APP_PATH%" -GuiTimeoutSeconds 90
exit /b %ERRORLEVEL%

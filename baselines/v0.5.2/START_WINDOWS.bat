@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title BeatSolver Timeline - shared environment launcher

set "BEATSOLVER_LOGDIR=%LOCALAPPDATA%\BeatSolverTimeline"
if not defined LOCALAPPDATA set "BEATSOLVER_LOGDIR=%TEMP%\BeatSolverTimeline"
if not exist "%BEATSOLVER_LOGDIR%" mkdir "%BEATSOLVER_LOGDIR%" 2>nul
set "BEATSOLVER_LOG=%BEATSOLVER_LOGDIR%\startup.log"
set "PYTHONUNBUFFERED=1"
set "PYTHONFAULTHANDLER=1"

>"%BEATSOLVER_LOG%" echo ===== BeatSolver Timeline startup =====
>>"%BEATSOLVER_LOG%" echo Folder: %CD%

echo.
echo ==== BEATSOLVER TIMELINE ====
echo Debug log: "%BEATSOLVER_LOG%"
if not exist "%~dp0beatsolver_timeline.py" (
    echo ERROR: beatsolver_timeline.py is missing.
    goto :failed
)
if not exist "%~dp0requirements.txt" (
    echo ERROR: requirements.txt is missing.
    goto :failed
)
call "%~dp0SETUP_WINDOWS.bat"
if errorlevel 1 goto :failed

echo.
echo Launching app using shared environment...
>>"%BEATSOLVER_LOG%" echo [Run] Starting beatsolver_timeline.py
"%BEATSOLVER_PY%" -X faulthandler "%~dp0beatsolver_timeline.py" >>"%BEATSOLVER_LOG%" 2>&1
set "APP_EXIT=%ERRORLEVEL%"
>>"%BEATSOLVER_LOG%" echo [Run] Exit code: %APP_EXIT%
if not "%APP_EXIT%"=="0" (
    echo ERROR: App exited with code %APP_EXIT%.
    goto :failed
)
echo.
echo Application closed normally. Closing launcher.
exit /b 0

:failed
echo.
echo ERROR: BeatSolver could not start.
echo Log: "%BEATSOLVER_LOG%"
powershell -NoProfile -Command "Get-Content -LiteralPath $env:BEATSOLVER_LOG -Tail 35" 2>nul

echo.
echo Press any key to close this window after an error.
pause >nul
exit /b 1

@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title BeatSolver Timeline - EXE builder using shared environment

set "BEATSOLVER_LOGDIR=%LOCALAPPDATA%\BeatSolverTimeline"
if not defined LOCALAPPDATA set "BEATSOLVER_LOGDIR=%TEMP%\BeatSolverTimeline"
if not exist "%BEATSOLVER_LOGDIR%" mkdir "%BEATSOLVER_LOGDIR%" 2>nul
set "BEATSOLVER_LOG=%BEATSOLVER_LOGDIR%\build.log"
>"%BEATSOLVER_LOG%" echo ===== BeatSolver Timeline EXE build =====
>>"%BEATSOLVER_LOG%" echo Folder: %CD%

echo.
echo ==== BEATSOLVER EXE BUILDER ====
echo Log: "%BEATSOLVER_LOG%"
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

"%BEATSOLVER_PY%" -c "import PyInstaller" >>"%BEATSOLVER_LOG%" 2>&1
if errorlevel 1 (
    echo Installing PyInstaller into shared environment...
    "%BEATSOLVER_PY%" -m pip install --disable-pip-version-check pyinstaller >>"%BEATSOLVER_LOG%" 2>&1
    if errorlevel 1 goto :failed
)
echo Building the EXE...
"%BEATSOLVER_PY%" -m PyInstaller --noconfirm --clean --console --onedir --name BeatSolverTimeline "%~dp0beatsolver_timeline.py" >>"%BEATSOLVER_LOG%" 2>&1
if errorlevel 1 goto :failed

echo.
echo BUILD COMPLETED: "%CD%\dist\BeatSolverTimeline\BeatSolverTimeline.exe"
echo Keep the full dist\BeatSolverTimeline folder.
goto :end

:failed
echo.
echo BUILD FAILED. Log: "%BEATSOLVER_LOG%"
powershell -NoProfile -Command "Get-Content -LiteralPath $env:BEATSOLVER_LOG -Tail 35" 2>nul

:end
echo.
echo Press any key to close this window.
pause >nul
exit /b 0

@echo off
setlocal
set "LOG_DIR=%LOCALAPPDATA%\BeatSolverTimeline"
if not defined LOCALAPPDATA set "LOG_DIR=%TEMP%\BeatSolverTimeline"
if exist "%LOG_DIR%\startup.log" (
  start "" notepad.exe "%LOG_DIR%\startup.log"
) else (
  echo No startup log yet. Run START_WINDOWS.bat first.
  pause
)

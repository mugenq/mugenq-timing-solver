@echo off
rem Shared venv for sibling BeatSolver version folders.
rem Expected layout:
rem   BeatSolver\.venv\
rem   BeatSolver\BeatSolver_Timeline_v0.3.1\
rem   BeatSolver\BeatSolver_Timeline_v0.4.0\
rem Called by START_WINDOWS.bat or BUILD_EXE_WINDOWS.bat.

for %%I in ("%~dp0..") do set "BEATSOLVER_ROOT=%%~fI"
set "BEATSOLVER_VENV=%BEATSOLVER_ROOT%\.venv"
set "BEATSOLVER_PY=%BEATSOLVER_VENV%\Scripts\python.exe"

echo Shared Python environment: "%BEATSOLVER_VENV%"
>>"%BEATSOLVER_LOG%" echo [Setup] Shared environment: %BEATSOLVER_VENV%

if exist "%BEATSOLVER_PY%" goto :validate_venv

echo Shared .venv not found. Finding 64-bit Python 3.11+...
set "BASE_PY="
for %%V in (3.13 3.12 3.11 3.14 3.15) do (
    if not defined BASE_PY (
        py -%%V -c "import sys; assert sys.maxsize > 2**32 and sys.version_info >= (3, 11)" >nul 2>nul
        if not errorlevel 1 set "BASE_PY=py -%%V"
    )
)
if not defined BASE_PY (
    python -c "import sys; assert sys.maxsize > 2**32 and sys.version_info >= (3, 11)" >nul 2>nul
    if not errorlevel 1 set "BASE_PY=python"
)
if not defined BASE_PY (
    echo ERROR: 64-bit Python 3.11+ is required.
    >>"%BEATSOLVER_LOG%" echo [ERROR] Python 3.11+ x64 was not found.
    exit /b 1
)
echo Creating shared environment using %BASE_PY% ...
>>"%BEATSOLVER_LOG%" echo [Setup] Creating shared .venv with %BASE_PY%
%BASE_PY% -m venv "%BEATSOLVER_VENV%" >>"%BEATSOLVER_LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Could not create the shared Python environment.
    exit /b 1
)

:validate_venv
if not exist "%BEATSOLVER_PY%" (
    echo ERROR: Shared environment has no Scripts\python.exe.
    exit /b 1
)
"%BEATSOLVER_PY%" -c "import sys; assert sys.maxsize > 2**32 and sys.version_info >= (3, 11)" >>"%BEATSOLVER_LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Shared environment uses an unsupported Python version.
    echo Move or rename the existing .venv and rerun this launcher.
    exit /b 1
)

rem Avoid pip when installed dependencies already import successfully.
"%BEATSOLVER_PY%" -c "import PySide6, numpy, miniaudio; from PySide6.QtMultimedia import QMediaPlayer" >>"%BEATSOLVER_LOG%" 2>&1
if not errorlevel 1 goto :good

echo Required packages missing. Installing into shared .venv once...
>>"%BEATSOLVER_LOG%" echo [Setup] Running pip install -r requirements.txt
"%BEATSOLVER_PY%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt" >>"%BEATSOLVER_LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Dependency installation failed. Check the log.
    exit /b 1
)
"%BEATSOLVER_PY%" -c "import PySide6, numpy, miniaudio; from PySide6.QtMultimedia import QMediaPlayer" >>"%BEATSOLVER_LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Packages were installed but import validation failed.
    exit /b 1
)

:good
>>"%BEATSOLVER_LOG%" echo [Setup] Shared dependency check passed.
echo Shared dependencies ready.
exit /b 0

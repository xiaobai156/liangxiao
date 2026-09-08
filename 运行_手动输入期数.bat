@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
if errorlevel 1 goto BAD_DIR

if not exist "two_zodiac_site_scraper.py" goto NO_SCRIPT

set "PYTHON_CMD="
where py.exe >nul 2>nul
if errorlevel 1 goto TRY_PYTHON_EXE
py.exe -3.10 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto TRY_PYTHON_EXE
set "PYTHON_CMD=py.exe -3.10"
goto PYTHON_READY

:TRY_PYTHON_EXE
where python.exe >nul 2>nul
if errorlevel 1 goto NO_PYTHON
python.exe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto NO_PYTHON
set "PYTHON_CMD=python.exe"

:PYTHON_READY

:ASK_PERIOD
set "PERIOD="
set /p "PERIOD=Input period number, example 182: "
if not defined PERIOD (
  echo Period is empty.
  goto ASK_PERIOD
)

echo !PERIOD!| findstr /r "^[0-9][0-9][0-9]$" >nul
if errorlevel 1 (
  echo Period must be exactly 3 digits.
  goto ASK_PERIOD
)

echo.
echo Work dir: %CD%
echo Command : !PYTHON_CMD! two_zodiac_site_scraper.py --period !PERIOD! --workers 10
echo.

!PYTHON_CMD! "two_zodiac_site_scraper.py" --period !PERIOD! --workers 10
set "RUN_ERROR=!ERRORLEVEL!"

echo.
if not "!RUN_ERROR!"=="0" echo Run failed. Error code: !RUN_ERROR!
pause
exit /b !RUN_ERROR!

:BAD_DIR
echo Cannot enter bat directory.
echo Bat dir: %~dp0
pause
exit /b 1

:NO_SCRIPT
echo Cannot find two_zodiac_site_scraper.py
echo Work dir: %CD%
pause
exit /b 1

:NO_PYTHON
echo Cannot find Python 3.10 via py.exe or python.exe in PATH.
pause
exit /b 1

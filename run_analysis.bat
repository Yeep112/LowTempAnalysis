@echo off
REM ============================================================
REM  Low-Temperature Analysis - opens the program.
REM  The console closes right away; the program keeps running.
REM  This file can be copied anywhere. Keep it ASCII-only.
REM
REM  run_analysis.bat              open the program
REM  run_analysis.bat some.db      open the program with that database
REM  run_analysis.bat some.toml    run a batch job file (in this window)
REM  run_analysis.bat menu         text menu (same as tools.bat)
REM  run_analysis.bat install      Start menu + desktop shortcuts
REM  run_analysis.bat diag         show diagnostics
REM  run_analysis.bat publish      developer: upload this version to GitHub
REM  run_analysis.bat receive      developer: receive Claude's pending update, test, sign, publish
REM  run_analysis.bat selftest     run the self test
REM ============================================================
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "LTA_DATA=%LOCALAPPDATA%\LowTempAnalysis"

REM ---- tool folder: next to this file, otherwise the remembered one ----
set "TOOL="
if exist "%~dp0lta\__main__.py" set "TOOL=%~dp0"
if not defined TOOL if exist "%LTA_DATA%\tool_path.txt" set /p TOOL=<"%LTA_DATA%\tool_path.txt"
if defined TOOL if not exist "%TOOL%\lta\__main__.py" set "TOOL="
if not defined TOOL goto notool
cd /d "%TOOL%"
call "%TOOL%\lta_env.bat"
if errorlevel 1 exit /b 1

set "ARG=%~1"
if not defined ARG goto gui
if /i "%ARG%"=="gui" goto gui
if /i "%ARG%"=="menu" goto menu
if /i "%ARG%"=="install" goto install
if /i "%ARG%"=="uninstall" goto uninstall
if /i "%ARG%"=="update" goto update
if /i "%ARG%"=="diag" goto diag
if /i "%ARG%"=="publish" goto publish
if /i "%ARG%"=="report" goto report
if /i "%ARG%"=="receive" goto receive
if /i "%ARG%"=="selftest" goto selftest
if /i "%~x1"==".db" goto guidb
"%PYEXE%" -m lta open "%~1"
pause
exit /b

:gui
start "" "%PYW%" -m lta app
exit /b

:guidb
start "" "%PYW%" -m lta app "%~1"
exit /b

:menu
"%PYEXE%" -m lta menu
exit /b

:install
"%PYEXE%" -m lta install
pause
exit /b

:uninstall
"%PYEXE%" -m lta uninstall
pause
exit /b

:update
"%PYEXE%" -m lta update
pause
exit /b

:diag
"%PYEXE%" -m lta diag
pause
exit /b

:report
"%PYEXE%" -m lta report
pause
exit /b

:receive
"%PYEXE%" -m lta receive
pause
exit /b

:selftest
"%PYEXE%" -m lta selftest
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%

:publish
"%PYEXE%" -m lta publish
pause
exit /b

:notool
echo.
echo  [ERROR] Cannot find the Low-Temperature Analysis folder.
echo  Please unzip the whole folder first (do not run it from inside the .zip),
echo  then run this file once from inside that folder.
echo  After that, copies of this file work from any location.
echo.
pause
exit /b 1

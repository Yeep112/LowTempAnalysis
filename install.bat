@echo off
REM ============================================================
REM  One-time setup: adds "Low-Temperature Analysis" to the Start menu
REM  and the desktop. Afterwards open it from there (no console window).
REM ============================================================
set "LTA_DATA=%LOCALAPPDATA%\LowTempAnalysis"
set "TOOL="
if exist "%~dp0lta\__main__.py" set "TOOL=%~dp0"
if not defined TOOL goto notool
call "%TOOL%\run_analysis.bat" install
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

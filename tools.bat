@echo off
REM ============================================================
REM  Text menu: batch analysis, data catalog, new job files, ...
REM  (The program itself is opened with run_analysis.bat.)
REM ============================================================
set "LTA_DATA=%LOCALAPPDATA%\LowTempAnalysis"
set "TOOL="
if exist "%~dp0lta\__main__.py" set "TOOL=%~dp0"
if not defined TOOL if exist "%LTA_DATA%\tool_path.txt" set /p TOOL=<"%LTA_DATA%\tool_path.txt"
if defined TOOL if not exist "%TOOL%\lta\__main__.py" set "TOOL="
if not defined TOOL goto notool
call "%TOOL%\run_analysis.bat" menu
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

@echo off
REM ============================================================
REM  Developer only: receive the update that Claude pushed to the
REM  "pending" branch, run the self test, sign it with your private
REM  key, and publish it to GitHub (co-workers then auto-update).
REM  Nothing is published if the test fails.
REM ============================================================
if not exist "%~dp0lta\__main__.py" goto notool
call "%~dp0run_analysis.bat" receive
exit /b

:notool
echo  [ERROR] Put this file inside the tool folder (next to run_analysis.bat).
pause
exit /b 1

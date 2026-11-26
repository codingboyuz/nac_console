@echo off
rem NAC Console: start.bat ochgan backend va frontend oynalarini (va ularning jarayonlarini) yopadi.
setlocal
set "SYS=%SystemRoot%\System32"
echo  NAC Console to'xtatilmoqda...
call :close "NAC backend" backend
call :close "NAC frontend" frontend

echo  Tayyor.
exit /b 0

:close
"%SYS%\tasklist.exe" /V /FI "WINDOWTITLE eq %~1*" 2>nul | "%SYS%\findstr.exe" /I "cmd.exe" >nul
if errorlevel 1 (
  echo   %~2 ishlamayapti edi
) else (
  "%SYS%\taskkill.exe" /FI "WINDOWTITLE eq %~1*" /T /F >nul 2>nul
  echo   %~2 to'xtatildi
)
exit /b 0

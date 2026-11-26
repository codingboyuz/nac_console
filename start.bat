@echo off
rem ============================================================================
rem  NAC Console - backend (FastAPI) va frontend (Next.js) ni bitta bosishda ishga
rem  tushiradi. Docker / PostgreSQL / Redis kerak emas: backend SQLite fayli
rem  (backend\.localdev\nac.db) va xotiradagi Redis bilan ishlaydi.
rem
rem  Ishga tushirish:  start.bat        To'xtatish:  stop.bat
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title NAC Console - ishga tushirish
set "SYS=%SystemRoot%\System32"
set "PS=%SYS%\WindowsPowerShell\v1.0\powershell.exe"

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"

echo.
echo  NAC Console ishga tushirilmoqda...
echo.

rem --- talablar -----------------------------------------------------------------
"%SYS%\where.exe" py >nul 2>nul || (
  echo  [XATO] Python topilmadi. Python 3.12 ni o'rnating: https://www.python.org/downloads/
  goto :fail
)
"%SYS%\where.exe" npm >nul 2>nul || (
  echo  [XATO] Node.js topilmadi. Node.js 20+ ni o'rnating: https://nodejs.org/
  goto :fail
)

rem --- portlar band emasligini tekshirish ----------------------------------------
call :port_busy %BACKEND_PORT% && (
  echo  [XATO] %BACKEND_PORT%-port band. Avval stop.bat ni ishga tushiring yoki uni band qilgan dasturni yoping.
  goto :fail
)
call :port_busy %FRONTEND_PORT% && (
  echo  [XATO] %FRONTEND_PORT%-port band. Avval stop.bat ni ishga tushiring yoki uni band qilgan dasturni yoping.
  goto :fail
)

rem --- bog'liqliklar (faqat birinchi marta) ---------------------------------------
if not exist "backend\.venv\Scripts\python.exe" (
  echo  [1/2] Backend kutubxonalari o'rnatilmoqda - birinchi marta, bir necha daqiqa...
  pushd backend
  py -3.12 -m uv --version >nul 2>nul || py -3.12 -m pip install --user uv || (popd & goto :fail)
  py -3.12 -m uv sync || (popd & goto :fail)
  popd
)
if not exist "frontend\node_modules" (
  echo  [2/2] Frontend kutubxonalari o'rnatilmoqda - birinchi marta, bir necha daqiqa...
  pushd frontend
  call npm install || (popd & goto :fail)
  popd
)

rem --- backend ----------------------------------------------------------------------
echo  Backend:  http://localhost:%BACKEND_PORT%   - alohida oynada
start "NAC backend" /D "%~dp0backend" cmd /k ".venv\Scripts\python.exe -u scripts\dev_run.py"
echo  Backend tayyor bo'lishi kutilmoqda...
call :wait_http "http://127.0.0.1:%BACKEND_PORT%/health/live" || (
  echo  [XATO] Backend 3 daqiqada ishga tushmadi. "NAC backend" oynasidagi xabarni tekshiring.
  goto :fail
)

rem --- frontend -----------------------------------------------------------------------
echo  Frontend: http://localhost:%FRONTEND_PORT%   - alohida oynada
start "NAC frontend" /D "%~dp0frontend" cmd /k "set BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%&& npm run dev -- -p %FRONTEND_PORT%"
echo  Frontend tayyor bo'lishi kutilmoqda...
call :wait_http "http://localhost:%FRONTEND_PORT%/login" || (
  echo  [XATO] Frontend 3 daqiqada ishga tushmadi. "NAC frontend" oynasidagi xabarni tekshiring.
  goto :fail
)

start "" "http://localhost:%FRONTEND_PORT%"
echo.
echo  ============================================================
echo   Tayyor!  Brauzer: http://localhost:%FRONTEND_PORT%
if exist "backend\.localdev\ADMIN_PASSWORD.txt" echo   Kirish ma'lumotlari: backend\.localdev\ADMIN_PASSWORD.txt
echo   To'xtatish uchun: stop.bat
echo  ============================================================
echo.
pause
exit /b 0

rem --- yordamchilar ------------------------------------------------------------------
:port_busy
"%SYS%\netstat.exe" -ano | "%SYS%\findstr.exe" /R /C:":%~1 .*LISTENING" >nul
exit /b %errorlevel%

:wait_http
rem 180 soniyagacha har soniyada so'raydi; 200 javob kelsa 0 bilan qaytadi.
"%PS%" -NoProfile -Command "$u='%~1'; for ($i=0; $i -lt 180; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 $u).StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
exit /b %errorlevel%

:fail
echo.
pause
exit /b 1

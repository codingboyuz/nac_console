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
rem Python 3.12+: avval "py -3.12" (python.org o'rnatuvchisi), bo'lmasa "python".
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo  [XATO] Python 3.12 yoki yangisi topilmadi.
  echo         O'rnating: https://www.python.org/downloads/  - "Add python.exe to PATH" belgisini qo'ying.
  goto :fail
)
"%SYS%\where.exe" npm >nul 2>nul || (
  echo  [XATO] Node.js topilmadi. Node.js 20 yoki yangisini o'rnating: https://nodejs.org/
  goto :fail
)
node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)" >nul 2>nul || (
  echo  [XATO] Node.js versiyasi eski. Node.js 20 yoki yangisini o'rnating: https://nodejs.org/
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

rem --- kutubxonalar: har safar tekshiriladi, faqat yetishmaganlari o'rnatiladi -------
rem Backend: uv "backend\.venv" ni yaratadi va pyproject.toml / uv.lock dagi kutubxonalarni
rem o'rnatadi (dev guruhi bilan: fakeredis, aiosqlite - lokal ishga tushirish uchun kerak).
echo  [1/2] Backend kutubxonalari tekshirilmoqda - backend\.venv
pushd backend
%PY% -m uv --version >nul 2>nul || (
  echo        uv o'rnatilmoqda...
  %PY% -m pip install --user --quiet --disable-pip-version-check uv || (popd & goto :fail_deps)
)
if not exist ".venv\Scripts\python.exe" echo        Birinchi marta: .venv yaratilmoqda, bir necha daqiqa kuting...
%PY% -m uv sync --quiet || (popd & goto :fail_deps)
popd

rem Frontend: package.json / package-lock.json dagi kutubxonalar frontend\node_modules ga.
echo  [2/2] Frontend kutubxonalari tekshirilmoqda - frontend\node_modules
pushd frontend
if not exist "node_modules" echo        Birinchi marta: kutubxonalar yuklanmoqda, bir necha daqiqa kuting...
call npm install --no-audit --no-fund --loglevel=error || (popd & goto :fail_deps)
popd
echo        Kutubxonalar tayyor.
echo.

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

:fail_deps
echo.
echo  [XATO] Kutubxonalarni o'rnatib bo'lmadi. Internet aloqasini (proksi bo'lsa sozlamasini)
echo         tekshiring va start.bat ni qayta ishga tushiring. Xato matni yuqorida.
goto :fail

:fail
echo.
pause
exit /b 1

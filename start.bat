@echo off
rem ============================================================================
rem  NAC Console - backend (FastAPI) va frontend (Next.js) ni bitta bosishda ishga
rem  tushiradi. Docker / PostgreSQL / Redis kerak emas: backend SQLite fayli
rem  (backend\.localdev\nac.db) va xotiradagi Redis bilan ishlaydi.
rem
rem  Ishga tushirish:  start.bat        To'xtatish:  stop.bat
rem  Sozlamalar:       nac.conf (IP/port, tarmoq manzili, admin login/parol);
rem                    birinchi ishga tushishda nac.conf.example dan yaratiladi.
rem  start.bat 192.168.15.152  - nac.conf dagi PUBLIC_HOST ni shu safar almashtiradi.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title NAC Console - ishga tushirish
set "SYS=%SystemRoot%\System32"
set "PS=%SYS%\WindowsPowerShell\v1.0\powershell.exe"

rem --- nac.conf (KALIT=QIYMAT) - birinchi marta shablondan yaratiladi -------------------
if not exist "nac.conf" if exist "nac.conf.example" (
  copy /y "nac.conf.example" "nac.conf" >nul
  set "CONF_CREATED=1"
)
for %%K in (BACKEND_HOST BACKEND_PORT FRONTEND_HOST FRONTEND_PORT PUBLIC_HOST ADMIN_USERNAME ADMIN_PASSWORD) do set "CONF_%%K="
if exist "nac.conf" for /f "usebackq eol=# tokens=1* delims==" %%A in ("nac.conf") do set "CONF_%%A=%%B"

rem Ustunlik: buyruq argumenti  >  muhit o'zgaruvchisi  >  nac.conf  >  standart qiymat
if not defined BACKEND_PORT set "BACKEND_PORT=%CONF_BACKEND_PORT%"
if not defined BACKEND_PORT set "BACKEND_PORT=8000"
if not defined FRONTEND_PORT set "FRONTEND_PORT=%CONF_FRONTEND_PORT%"
if not defined FRONTEND_PORT set "FRONTEND_PORT=3000"
if not "%~1"=="" set "NAC_PUBLIC_HOST=%~1"
if not defined NAC_PUBLIC_HOST set "NAC_PUBLIC_HOST=%CONF_PUBLIC_HOST%"
set "BACKEND_BIND=%NAC_HOST%"
if not defined BACKEND_BIND set "BACKEND_BIND=%CONF_BACKEND_HOST%"
if not defined BACKEND_BIND set "BACKEND_BIND=127.0.0.1"
set "FRONTEND_BIND=%FRONTEND_HOST%"
if not defined FRONTEND_BIND set "FRONTEND_BIND=%CONF_FRONTEND_HOST%"
if not defined FRONTEND_BIND set "FRONTEND_BIND=127.0.0.1"
set "ADMIN_USER=%NAC_ADMIN_USERNAME%"
if not defined ADMIN_USER set "ADMIN_USER=%CONF_ADMIN_USERNAME%"
if not defined ADMIN_USER set "ADMIN_USER=admin"
set "ADMIN_PW_SET="
if defined NAC_ADMIN_PASSWORD set "ADMIN_PW_SET=1"
if defined CONF_ADMIN_PASSWORD set "ADMIN_PW_SET=1"
set "CONF_ADMIN_PASSWORD="

set "BROWSER_HOST=localhost"
if defined NAC_PUBLIC_HOST (
  rem Boshqa kompyuterlardan kirish: panel va backend - WebSocket uchun - barcha interfeyslarda.
  set "BROWSER_HOST=%NAC_PUBLIC_HOST%"
  if "%FRONTEND_BIND%"=="127.0.0.1" set "FRONTEND_BIND=0.0.0.0"
  if "%BACKEND_BIND%"=="127.0.0.1" set "BACKEND_BIND=0.0.0.0"
)
set "NAC_HOST=%BACKEND_BIND%"
set "NAC_PORT=%BACKEND_PORT%"

rem Shu kompyuterdan xizmatlarga murojaat manzili (tekshiruvlar va frontend -> backend uchun).
set "BACKEND_LOCAL=%BACKEND_BIND%"
if "%BACKEND_BIND%"=="0.0.0.0" set "BACKEND_LOCAL=127.0.0.1"
if "%BACKEND_BIND%"=="localhost" set "BACKEND_LOCAL=127.0.0.1"
set "FRONTEND_LOCAL=%FRONTEND_BIND%"
if "%FRONTEND_BIND%"=="0.0.0.0" set "FRONTEND_LOCAL=127.0.0.1"
if "%FRONTEND_BIND%"=="localhost" set "FRONTEND_LOCAL=127.0.0.1"

echo.
echo  NAC Console ishga tushirilmoqda...
echo.
if defined CONF_CREATED echo  nac.conf yaratildi - IP/port va admin parolini shu faylda o'zgartiring.
echo  Sozlamalar: nac.conf  - backend %BACKEND_BIND%:%BACKEND_PORT%, frontend %FRONTEND_BIND%:%FRONTEND_PORT%
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
echo  Backend:  http://%BROWSER_HOST%:%BACKEND_PORT%   - alohida oynada
start "NAC backend" /D "%~dp0backend" cmd /k ".venv\Scripts\python.exe -u scripts\dev_run.py"
echo  Backend tayyor bo'lishi kutilmoqda...
call :wait_http "http://%BACKEND_LOCAL%:%BACKEND_PORT%/health/live" || (
  echo  [XATO] Backend 3 daqiqada ishga tushmadi. "NAC backend" oynasidagi xabarni tekshiring.
  goto :fail
)

rem --- frontend -----------------------------------------------------------------------
echo  Frontend: http://%BROWSER_HOST%:%FRONTEND_PORT%   - alohida oynada
rem COOKIE_SECURE=false: lokal runner http orqali ishlaydi; NEXT_PUBLIC_WS_URL: brauzer -> backend WebSocket.
start "NAC frontend" /D "%~dp0frontend" cmd /k "set BACKEND_URL=http://%BACKEND_LOCAL%:%BACKEND_PORT%&& set COOKIE_SECURE=false&& set NEXT_PUBLIC_WS_URL=ws://%BROWSER_HOST%:%BACKEND_PORT%&& set NAC_PUBLIC_HOST=%NAC_PUBLIC_HOST%&& npm run dev -- -H %FRONTEND_BIND% -p %FRONTEND_PORT%"
echo  Frontend tayyor bo'lishi kutilmoqda...
call :wait_http "http://%FRONTEND_LOCAL%:%FRONTEND_PORT%/login" || (
  echo  [XATO] Frontend 3 daqiqada ishga tushmadi. "NAC frontend" oynasidagi xabarni tekshiring.
  goto :fail
)

start "" "http://%BROWSER_HOST%:%FRONTEND_PORT%"
echo.
echo  ============================================================
echo   Tayyor!  Brauzer: http://%BROWSER_HOST%:%FRONTEND_PORT%
if defined NAC_PUBLIC_HOST echo   Tarmoq: %FRONTEND_PORT% va %BACKEND_PORT% portlarini Windows Firewall da oching.
if defined ADMIN_PW_SET (
  echo   Kirish: login %ADMIN_USER%, parol: nac.conf dagi ADMIN_PASSWORD
) else if exist "backend\.localdev\ADMIN_PASSWORD.txt" (
  echo   Kirish ma'lumotlari: backend\.localdev\ADMIN_PASSWORD.txt
)
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

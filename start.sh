#!/usr/bin/env bash
# =============================================================================
#  NAC Console - backend (FastAPI) va frontend (Next.js) ni bitta buyruq bilan
#  ishga tushiradi (Linux / macOS). Docker / PostgreSQL / Redis kerak emas:
#  backend SQLite fayli (backend/.localdev/nac.db) va xotiradagi Redis bilan ishlaydi.
#
#  Ishga tushirish:  ./start.sh          To'xtatish:  ./stop.sh
#  Sozlamalar:       nac.conf  (IP/port, tarmoq manzili, admin login/parol)
#                    birinchi ishga tushishda nac.conf.example dan yaratiladi
#  ./start.sh --host 192.168.15.152   nac.conf dagi PUBLIC_HOST ni shu safar almashtiradi
#  ./start.sh --deps  o'rnatilgan asosiy kutubxonalar ro'yxatini har doim ko'rsatadi
#  Loglar:           backend/.localdev/logs/{backend,frontend}.log
#
#  Muhit o'zgaruvchilari nac.conf dan ustun turadi: NAC_PUBLIC_HOST, NAC_HOST, BACKEND_PORT,
#  FRONTEND_HOST, FRONTEND_PORT, NAC_ADMIN_USERNAME, NAC_ADMIN_PASSWORD, NAC_DATABASE_URL ...
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
RUN="$BACKEND/.localdev/run"
LOGS="$BACKEND/.localdev/logs"
# --- nac.conf (KALIT=QIYMAT): birinchi marta shablondan yaratiladi ----------------------
CONF="${NAC_CONF:-$ROOT/nac.conf}"
export NAC_CONF="$CONF"
if [ ! -f "$CONF" ] && [ -f "$ROOT/nac.conf.example" ]; then
  cp "$ROOT/nac.conf.example" "$CONF"
  chmod 600 "$CONF" 2>/dev/null || true
  CONF_CREATED=1
fi
conf_get() {  # conf_get KALIT -> qiymat (bo'sh bo'lishi mumkin)
  [ -f "$CONF" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CONF" | tail -n 1 \
    | tr -d '\r' | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
# Ustunlik: --host  >  muhit o'zgaruvchisi  >  nac.conf  >  standart qiymat
BACKEND_PORT="${BACKEND_PORT:-$(conf_get BACKEND_PORT)}"; BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-$(conf_get FRONTEND_PORT)}"; FRONTEND_PORT="${FRONTEND_PORT:-3000}"
NAC_HOST="${NAC_HOST:-$(conf_get BACKEND_HOST)}"; NAC_HOST="${NAC_HOST:-127.0.0.1}"
FRONTEND_HOST="${FRONTEND_HOST:-$(conf_get FRONTEND_HOST)}"; FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
ADMIN_USER="${NAC_ADMIN_USERNAME:-$(conf_get ADMIN_USERNAME)}"; ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PW_SET=""; [ -n "${NAC_ADMIN_PASSWORD:-$(conf_get ADMIN_PASSWORD)}" ] && ADMIN_PW_SET=1
for v in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  case "$v" in ''|*[!0-9]*) echo "nac.conf: port raqam bo'lishi kerak: $v" >&2; exit 2 ;; esac
done
export NAC_HOST NAC_PORT="$BACKEND_PORT"
# Kesh va loyiha turli disklarda bo'lsa ham uv ogohlantirmasin (nusxalab o'rnatadi).
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
SHOW_DEPS=0
PUBLIC_HOST="${NAC_PUBLIC_HOST:-$(conf_get PUBLIC_HOST)}"
while [ $# -gt 0 ]; do
  case "$1" in
    --deps|-d) SHOW_DEPS=1 ;;
    --host) [ $# -ge 2 ] || { echo "--host dan keyin IP yoki hostname yozing" >&2; exit 2; }; PUBLIC_HOST="$2"; shift ;;
    --host=*) PUBLIC_HOST="${1#--host=}" ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) printf "Noma'lum parametr: %s  (./start.sh --help)\n" "$1" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$PUBLIC_HOST" ]; then
  case "$PUBLIC_HOST" in
    *[!A-Za-z0-9.:-]*) echo "Noto'g'ri PUBLIC_HOST / --host qiymati: $PUBLIC_HOST" >&2; exit 2 ;;
  esac
  # Boshqa kompyuterlardan kirish: panel va backend (WebSocket uchun) barcha interfeyslarda tinglaydi.
  case "$FRONTEND_HOST" in 127.0.0.1|localhost) FRONTEND_HOST="0.0.0.0" ;; esac
  case "$NAC_HOST" in 127.0.0.1|localhost) NAC_HOST="0.0.0.0" ;; esac
fi
export NAC_HOST NAC_PUBLIC_HOST="$PUBLIC_HOST"
# Shu mashinadan xizmatlarga murojaat manzili (tekshiruvlar va frontend -> backend uchun).
local_addr() { case "$1" in 0.0.0.0|127.0.0.1|localhost|::|"") echo 127.0.0.1 ;; *) echo "$1" ;; esac; }
BACKEND_LOCAL="$(local_addr "$NAC_HOST")"
FRONTEND_LOCAL="$(local_addr "$FRONTEND_HOST")"
BROWSER_HOST="${PUBLIC_HOST:-localhost}"

if [ -t 1 ]; then R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'; else R=; G=; Y=; B=; N=; fi
info() { printf ' %s\n' "$*"; }
step() { printf ' %s%s%s\n' "$B" "$*" "$N"; }
fail() { printf '\n %s[XATO]%s %s\n\n' "$R" "$N" "$*" >&2; exit 1; }

printf '\n %sNAC Console ishga tushirilmoqda...%s\n\n' "$B" "$N"
[ -n "${CONF_CREATED:-}" ] && info "nac.conf yaratildi (nac.conf.example asosida) - IP/port va admin parolini shu yerda o'zgartiring."
info "Sozlamalar: nac.conf  (backend $NAC_HOST:$BACKEND_PORT, frontend $FRONTEND_HOST:$FRONTEND_PORT${PUBLIC_HOST:+, tarmoq $PUBLIC_HOST})"
echo

# --- talablar -------------------------------------------------------------------------
# Har qanday python3 yetarli: uv kerakli Python 3.12 ni o'zi topadi yoki yuklab oladi.
PY=""
for cand in python3.13 python3.12 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || fail "Python 3 topilmadi. O'rnating: sudo apt install python3 python3-pip   (Fedora: sudo dnf install python3)"

command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 \
  || fail "Node.js topilmadi. Node.js 20+ o'rnating: https://nodejs.org/  (yoki: https://github.com/nvm-sh/nvm)"
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)' \
  || fail "Node.js versiyasi eski ($(node -v)). Node.js 20 yoki yangisini o'rnating."

# --- portlar band emasligini tekshirish -------------------------------------------------
port_busy() {  # port_busy <manzil> <port>
  "$PY" - "$1" "$2" <<'EOF'
import socket, sys
s = socket.socket()
s.settimeout(0.5)
sys.exit(0 if s.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else 1)
EOF
}
port_busy "$BACKEND_LOCAL" "$BACKEND_PORT" && fail "$BACKEND_PORT-port band. Avval ./stop.sh ni ishga tushiring yoki uni band qilgan dasturni to'xtating."
port_busy "$FRONTEND_LOCAL" "$FRONTEND_PORT" && fail "$FRONTEND_PORT-port band. Avval ./stop.sh ni ishga tushiring yoki uni band qilgan dasturni to'xtating."

# --- kutubxonalar: har safar tekshiriladi, faqat yetishmaganlari o'rnatiladi -----------
deps_fail() { fail "Kutubxonalarni o'rnatib bo'lmadi. Internet aloqasini (proksi bo'lsa sozlamasini) tekshiring va qayta urining. Xato matni yuqorida."; }

UV=""
if command -v uv >/dev/null 2>&1; then UV="uv"
elif "$PY" -m uv --version >/dev/null 2>&1; then UV="$PY -m uv"
elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"
fi
if [ -z "$UV" ]; then
  info "uv o'rnatilmoqda..."
  if "$PY" -m pip install --user --quiet --disable-pip-version-check uv 2>/dev/null; then UV="$PY -m uv"
  elif command -v pipx >/dev/null 2>&1 && pipx install uv >/dev/null; then UV="$HOME/.local/bin/uv"
  else
    fail "uv o'rnatilmadi. Qo'lda o'rnating va qayta urining:
         sudo apt install pipx && pipx install uv        (Debian/Ubuntu)
         yoki: https://docs.astral.sh/uv/getting-started/installation/"
  fi
fi

indent() { sed 's/^/      /'; }
DEPS_LOG="$(mktemp)"
trap 'rm -f "$DEPS_LOG"' EXIT

step "[1/2] Backend kutubxonalari (Python) - backend/.venv"
[ -d "$BACKEND/.venv" ] || info "      Birinchi marta: .venv yaratilmoqda, bir necha daqiqa kuting..."
# uv har bir o'rnatilgan / yangilangan / o'chirilgan paketni "+ nom==versiya" ko'rinishida chiqaradi.
if ! (cd "$BACKEND" && $UV sync --color never) 2>&1 | tee "$DEPS_LOG" | indent; then deps_fail; fi
if [ "$SHOW_DEPS" = 1 ] || grep -qE '^ *[-+~] ' "$DEPS_LOG"; then
  info "     Asosiy backend kutubxonalari (to'g'ridan-to'g'ri bog'liqliklar):"
  (cd "$BACKEND" && $UV tree --depth 1 --frozen --color never 2>/dev/null) | tail -n +2 | indent || true
fi
echo

step "[2/2] Frontend kutubxonalari (Node.js) - frontend/node_modules"
[ -d "$FRONTEND/node_modules" ] || info "      Birinchi marta: kutubxonalar yuklanmoqda, bir necha daqiqa kuting..."
if ! (cd "$FRONTEND" && npm install --no-audit --no-fund --color=false) 2>&1 | tee "$DEPS_LOG" | grep -v '^$' | indent; then deps_fail; fi
if [ "$SHOW_DEPS" = 1 ] || grep -qE 'added|changed|removed' "$DEPS_LOG"; then
  info "     Asosiy frontend kutubxonalari:"
  (cd "$FRONTEND" && npm ls --depth=0 --color=false 2>/dev/null) | tail -n +2 | grep -v -e extraneous -e '^$' | indent || true
fi
info "      Kutubxonalar tayyor.  (To'liq ro'yxat har doim: ./start.sh --deps)"
echo

VENV_PY="$BACKEND/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$BACKEND/.venv/Scripts/python.exe"   # Git Bash / Windows
[ -x "$VENV_PY" ] || fail "backend/.venv ichida python topilmadi."

# --- ishga tushirish ----------------------------------------------------------------------
mkdir -p "$RUN" "$LOGS"
# setsid: har bir xizmat o'z jarayon guruhida - stop.sh bolalarini ham to'xtata oladi.
launch() {  # launch <nom> <papka> <buyruq...>
  local name="$1" dir="$2"; shift 2
  if command -v setsid >/dev/null 2>&1; then
    (cd "$dir" && exec setsid "$@" >"$LOGS/$name.log" 2>&1 < /dev/null) &
  else
    (cd "$dir" && exec "$@" >"$LOGS/$name.log" 2>&1 < /dev/null) &
  fi
  echo $! > "$RUN/$name.pid"
}

wait_http() {  # wait_http <url> <pid-nomi>: 180 soniyagacha kutadi
  local url="$1" name="$2" i
  for i in $(seq 1 180); do
    if "$PY" - "$url" <<'EOF' 2>/dev/null
import sys, urllib.request
sys.exit(0 if urllib.request.urlopen(sys.argv[1], timeout=5).status == 200 else 1)
EOF
    then return 0; fi
    kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null || return 1   # jarayon to'xtab qoldi
    sleep 1
  done
  return 1
}

info "Backend:  http://$NAC_HOST:$BACKEND_PORT   (log: backend/.localdev/logs/backend.log)"
launch backend "$BACKEND" "$VENV_PY" -u scripts/dev_run.py
info "Backend tayyor bo'lishi kutilmoqda..."
if ! wait_http "http://$BACKEND_LOCAL:$BACKEND_PORT/health/live" backend; then
  tail -n 25 "$LOGS/backend.log" >&2 || true
  "$ROOT/stop.sh" >/dev/null 2>&1 || true
  fail "Backend ishga tushmadi. To'liq log: backend/.localdev/logs/backend.log"
fi

info "Frontend: http://$FRONTEND_HOST:$FRONTEND_PORT   (log: backend/.localdev/logs/frontend.log)"
# COOKIE_SECURE=false: bu lokal runner http orqali ishlaydi (Secure cookie faqat https da saqlanadi).
# NEXT_PUBLIC_WS_URL: brauzer WebSocket ni to'g'ridan-to'g'ri backendga ochadi.
BACKEND_URL="http://$BACKEND_LOCAL:$BACKEND_PORT" COOKIE_SECURE=false \
  NEXT_PUBLIC_WS_URL="ws://$BROWSER_HOST:$BACKEND_PORT" NAC_PUBLIC_HOST="$PUBLIC_HOST" \
  launch frontend "$FRONTEND" npm run dev -- -H "$FRONTEND_HOST" -p "$FRONTEND_PORT"
info "Frontend tayyor bo'lishi kutilmoqda..."
if ! wait_http "http://$FRONTEND_LOCAL:$FRONTEND_PORT/login" frontend; then
  tail -n 25 "$LOGS/frontend.log" >&2 || true
  "$ROOT/stop.sh" >/dev/null 2>&1 || true
  fail "Frontend ishga tushmadi. To'liq log: backend/.localdev/logs/frontend.log"
fi

URL="http://$BROWSER_HOST:$FRONTEND_PORT"
if [ -z "$PUBLIC_HOST" ] && [ "$FRONTEND_HOST" = "0.0.0.0" ]; then
  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$LAN_IP" ] && URL="http://$LAN_IP:$FRONTEND_PORT"
fi
echo
printf ' %s============================================================%s\n' "$G" "$N"
printf '  %sTayyor!%s  Brauzer: %s\n' "$G" "$N" "$URL"
if [ -n "$ADMIN_PW_SET" ]; then
  printf '  Kirish:   login %s, parol: nac.conf dagi ADMIN_PASSWORD\n' "$ADMIN_USER"
elif [ -f "$BACKEND/.localdev/ADMIN_PASSWORD.txt" ]; then
  printf '  Kirish:   login %s, parol: %s\n' "$(sed -n 1p "$BACKEND/.localdev/ADMIN_PASSWORD.txt")" \
    "$(sed -n 2p "$BACKEND/.localdev/ADMIN_PASSWORD.txt")"
else
  printf '  Kirish:   login %s\n' "$ADMIN_USER"
fi
printf '  Loglar:   tail -f backend/.localdev/logs/*.log\n'
if [ -n "$PUBLIC_HOST" ]; then
  printf '  Tarmoq:   %s:%s (panel) va %s (backend/WebSocket) ochiq bo%sldi.\n' "$PUBLIC_HOST" "$FRONTEND_PORT" "$BACKEND_PORT" "'"
  printf '            Firewall: sudo ufw allow %s/tcp && sudo ufw allow %s/tcp\n' "$FRONTEND_PORT" "$BACKEND_PORT"
fi
printf '  To%sxtatish: ./stop.sh\n' "'"
printf ' %s============================================================%s\n\n' "$G" "$N"
command -v xdg-open >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && xdg-open "$URL" >/dev/null 2>&1 || true

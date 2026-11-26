#!/usr/bin/env bash
# NAC Console: start.sh ishga tushirgan backend va frontendni (bolalari bilan) to'xtatadi.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$ROOT/backend/.localdev/run"
conf_get() {  # nac.conf dan KALIT=QIYMAT
  local conf="${NAC_CONF:-$ROOT/nac.conf}"
  [ -f "$conf" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$conf" | tail -n 1 | tr -d '\r' | sed 's/[[:space:]]*$//'
}
BACKEND_PORT="${BACKEND_PORT:-$(conf_get BACKEND_PORT)}"; BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-$(conf_get FRONTEND_PORT)}"; FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# Port hali band bo'lsa (masalan npm -> node bola jarayoni qolgan bo'lsa), uni tinglayotgan
# jarayonni to'xtatamiz - faqat node/python bo'lsa, boshqa dasturlarga tegmaymiz.
free_port() {
  local port="$1" pids pid name
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 || true)"
  elif command -v netstat.exe >/dev/null 2>&1 || [ -x /c/Windows/System32/NETSTAT.EXE ]; then
    # Git Bash / Windows: Windows PID lar
    pids="$(netstat -ano 2>/dev/null | awk -v p=":$port" '$2 ~ p"$" && $4 == "LISTENING" {print $5}' | sort -u)"
    for pid in $pids; do
      name="$(tasklist //FI "PID eq $pid" //FO CSV //NH 2>/dev/null | cut -d, -f1 | tr -d '"')"
      case "$name" in node.exe|python.exe) taskkill //PID "$pid" //T //F >/dev/null 2>&1 ;; esac
    done
    return
  else
    return
  fi
  for pid in $pids; do
    name="$(ps -o comm= -p "$pid" 2>/dev/null || true)"
    case "$name" in *node*|*python*|*next-server*) kill -TERM "$pid" 2>/dev/null ;; esac
  done
}

echo " NAC Console to'xtatilmoqda..."
stop_one() {  # stop_one <nom> <port>
  local name="$1" port="$2" file="$RUN/$1.pid" pid i stopped=0
  if [ -f "$file" ] && pid="$(cat "$file")" && kill -0 "$pid" 2>/dev/null; then
    # Avval butun jarayon guruhini (setsid), bo'lmasa jarayonning o'zini ohista to'xtatamiz.
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    pkill -TERM -P "$pid" 2>/dev/null || true
    for i in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    fi
    stopped=1
  fi
  rm -f "$file"
  free_port "$port"
  if [ "$stopped" = 1 ]; then echo "   $name to'xtatildi"; else echo "   $name ishlamayapti edi"; fi
}
stop_one frontend "$FRONTEND_PORT"
stop_one backend "$BACKEND_PORT"
echo " Tayyor."

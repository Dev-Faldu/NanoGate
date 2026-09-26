#!/usr/bin/env bash
# Manage the NanoGate gateway process (serves API + built dashboard).
# HTTPS: set NANOGATE_TLS_CERT and NANOGATE_TLS_KEY in .env (scripts/make_tls_cert.sh creates a self-signed pair).
# Usage: scripts/gateway.sh start|stop|restart|status|run   (run = foreground, used by the systemd unit)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDF="$ROOT/var/gateway.pid"
LOG="$ROOT/var/gateway.out"
HOST="${NANOGATE_HOST:-127.0.0.1}"
PORT="${NANOGATE_PORT:-8080}"
mkdir -p "$ROOT/var"
# TLS paths from the environment or .env (relative paths are relative to the project root)
for k in NANOGATE_TLS_CERT NANOGATE_TLS_KEY; do
  if [[ -z "${!k:-}" && -f "$ROOT/.env" ]]; then v="$(sed -n "s/^$k=//p" "$ROOT/.env" | tail -1 | tr -d "\"'")"; [[ -n "$v" ]] && export "$k=$v"; fi
done
TLS_ARGS=()
SCHEME=http
if [[ -n "${NANOGATE_TLS_CERT:-}" && -n "${NANOGATE_TLS_KEY:-}" ]]; then
  [[ "$NANOGATE_TLS_CERT" = /* ]] || NANOGATE_TLS_CERT="$ROOT/$NANOGATE_TLS_CERT"
  [[ "$NANOGATE_TLS_KEY" = /* ]] || NANOGATE_TLS_KEY="$ROOT/$NANOGATE_TLS_KEY"
  export NANOGATE_TLS_CERT NANOGATE_TLS_KEY
  TLS_ARGS=(--ssl-certfile "$NANOGATE_TLS_CERT" --ssl-keyfile "$NANOGATE_TLS_KEY")
  SCHEME=https
fi
URL="$SCHEME://$HOST:$PORT"

up() { curl -sfk -m 2 "$URL/healthz" >/dev/null 2>&1; }

start() {
  if up; then echo "gateway: already running on $URL"; return 0; fi
  (cd "$ROOT/backend" && setsid nohup "$ROOT/.venv/bin/uvicorn" nanogate.main:create_app --factory \
      --host "$HOST" --port "$PORT" "${TLS_ARGS[@]}" --log-level warning >>"$LOG" 2>&1 < /dev/null & echo $! > "$PIDF")
  for _ in $(seq 1 120); do up && { echo "gateway: up on $URL (pid $(cat "$PIDF"))"; return 0; }; sleep 1; done
  echo "gateway: failed to start; see $LOG" >&2; return 1
}

port_pid() { ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true; }

wait_down() {
  for _ in $(seq 1 30); do up || return 0; sleep 1; done
  pid="$(port_pid)"; [[ -n "${pid:-}" ]] && kill -9 "$pid" 2>/dev/null || true; sleep 1
}

stop() {
  stop_signal; wait_down
}

stop_signal() {
  if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    kill "$(cat "$PIDF")"; rm -f "$PIDF"; echo "gateway: stopping"
  else
    # fall back to the process listening on our port (never pattern-matches our own shell)
    pid="$(port_pid)"
    if [[ -n "${pid:-}" ]]; then kill "$pid"; echo "gateway: stopped pid $pid"; else echo "gateway: not running"; fi
    rm -f "$PIDF"
  fi
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) if up; then echo "gateway: up on $URL"; else echo "gateway: down"; exit 1; fi ;;
  run) cd "$ROOT/backend" && exec "$ROOT/.venv/bin/uvicorn" nanogate.main:create_app --factory \
         --host "$HOST" --port "$PORT" "${TLS_ARGS[@]}" --log-level warning ;;
  *) echo "usage: $0 start|stop|restart|status|run" >&2; exit 2 ;;
esac

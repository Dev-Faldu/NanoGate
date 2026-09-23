#!/usr/bin/env bash
# Manage the project-local Ollama inference runtime (GPU, user-space, no root).
# Usage: scripts/runtime.sh start|stop|status
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RT="$ROOT/.runtime"
BIN="$RT/ollama/bin/ollama"
PIDF="$RT/ollama.pid"
LOG="$RT/logs/ollama.log"
HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
mkdir -p "$RT/logs" "$RT/ollama-models"

is_up() { curl -sf -m 2 "http://$HOST/api/version" >/dev/null 2>&1; }

case "${1:-status}" in
  start)
    if is_up; then echo "ollama: already running at $HOST"; exit 0; fi
    if [[ ! -x "$BIN" ]]; then echo "ollama: binary missing at $BIN (run make setup)" >&2; exit 1; fi
    OLLAMA_MODELS="$RT/ollama-models" OLLAMA_HOST="$HOST" \
    OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-4}" OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-3}" \
    OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-24h}" OLLAMA_FLASH_ATTENTION=1 \
      setsid nohup "$BIN" serve >>"$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    for _ in $(seq 1 60); do is_up && { echo "ollama: up at $HOST (pid $(cat "$PIDF"))"; exit 0; }; sleep 0.5; done
    echo "ollama: failed to start, see $LOG" >&2; exit 1 ;;
  stop)
    if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then kill "$(cat "$PIDF")"; rm -f "$PIDF"; echo "ollama: stopped"; else echo "ollama: not running (no pid file)"; fi ;;
  status)
    if is_up; then echo "ollama: up at $HOST"; else echo "ollama: down"; exit 1; fi ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac

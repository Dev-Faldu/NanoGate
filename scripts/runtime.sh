#!/usr/bin/env bash
# Manage the project-local vLLM inference servers on the GB10 (user-space, no root).
# vLLM serves one model per process: the local tier and the local-large (escalation) tier each get a server.
# Usage: scripts/runtime.sh start|stop|restart|status [local|large|all]   (default: all)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RT="$ROOT/.runtime"
BIN="$RT/vllm/bin/vllm"
# same model settings as the gateway: .env fills in anything not already set in the environment
if [[ -f "$ROOT/.env" ]]; then
  while IFS='=' read -r k v; do
    v="$(sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//' <<<"$v")"
    [[ -z "${!k:-}" && -n "$v" ]] && export "$k=$v"
  done < <(grep -E '^(LOCAL_|VLLM_|HF_HOME)[A-Z_]*=' "$ROOT/.env")
fi
export HF_HOME="${HF_HOME:-$RT/hf}"
[[ "$HF_HOME" = /* ]] || HF_HOME="$ROOT/$HF_HOME"
mkdir -p "$RT/logs"

# tier -> model, port, share of GPU memory (GB10 unified memory), max context
cfg() {
  case "$1" in
    local) MODEL="${LOCAL_MODEL_NAME:-Qwen/Qwen2.5-3B-Instruct}"; PORT="${VLLM_LOCAL_PORT:-8000}"
           UTIL="${VLLM_LOCAL_GPU_UTIL:-0.15}"; MAXLEN="${VLLM_MAX_MODEL_LEN:-8192}" ;;
    large) MODEL="${LOCAL_LARGE_MODEL_NAME:-Qwen/Qwen2.5-14B-Instruct}"; PORT="${VLLM_LARGE_PORT:-8001}"
           UTIL="${VLLM_LARGE_GPU_UTIL:-0.40}"; MAXLEN="${VLLM_MAX_MODEL_LEN:-8192}" ;;
    *) echo "unknown tier: $1 (local|large)" >&2; exit 2 ;;
  esac
  PIDF="$RT/vllm-$1.pid"; LOG="$RT/logs/vllm-$1.log"
}

is_up() { curl -sf -m 2 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; }

start_tier() {
  cfg "$1"
  if is_up; then echo "vllm[$1]: already running on :$PORT ($MODEL)"; return 0; fi
  if [[ ! -x "$BIN" ]]; then echo "vllm: binary missing at $BIN (run make setup)" >&2; return 1; fi
  setsid nohup "$BIN" serve "$MODEL" --served-model-name "$MODEL" --host 127.0.0.1 --port "$PORT" \
      --gpu-memory-utilization "$UTIL" --max-model-len "$MAXLEN" --max-logprobs 5 \
      >>"$LOG" 2>&1 < /dev/null &
  echo $! > "$PIDF"
  # first start compiles kernels and captures CUDA graphs: allow several minutes
  for _ in $(seq 1 600); do
    is_up && { echo "vllm[$1]: up on :$PORT ($MODEL, pid $(cat "$PIDF"))"; return 0; }
    kill -0 "$(cat "$PIDF")" 2>/dev/null || { echo "vllm[$1]: exited during startup, see $LOG" >&2; return 1; }
    sleep 1
  done
  echo "vllm[$1]: not ready after 600 s, see $LOG" >&2; return 1
}

stop_tier() {
  cfg "$1"
  if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    kill -- -"$(cat "$PIDF")" 2>/dev/null || kill "$(cat "$PIDF")"   # whole group: API server + engine core
    for _ in $(seq 1 30); do kill -0 "$(cat "$PIDF")" 2>/dev/null || break; sleep 1; done
    rm -f "$PIDF"; echo "vllm[$1]: stopped"
  else
    rm -f "$PIDF"; echo "vllm[$1]: not running (no pid file)"
  fi
}

status_tier() {
  cfg "$1"
  if is_up; then echo "vllm[$1]: up on :$PORT ($MODEL)"; else echo "vllm[$1]: down"; return 1; fi
}

TIERS=(local large)
case "${2:-all}" in all) ;; local|large) TIERS=("$2") ;; *) echo "unknown tier: $2" >&2; exit 2 ;; esac

case "${1:-status}" in
  start) for t in "${TIERS[@]}"; do start_tier "$t"; done ;;
  stop) for t in "${TIERS[@]}"; do stop_tier "$t"; done ;;
  restart) for t in "${TIERS[@]}"; do stop_tier "$t"; start_tier "$t"; done ;;
  status) rc=0; for t in "${TIERS[@]}"; do status_tier "$t" || rc=1; done; exit $rc ;;
  *) echo "usage: $0 start|stop|restart|status [local|large|all]" >&2; exit 2 ;;
esac

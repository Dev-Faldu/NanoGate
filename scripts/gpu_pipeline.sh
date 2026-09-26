#!/usr/bin/env bash
# Resumable GPU pipeline: router data -> router training -> gateway reload -> attack suite -> load test
# -> end-to-end -> offline test -> RESULTS.md. Each finished step writes var/pipeline/<step>.done, so a
# rerun (e.g. from the @reboot crontab entry after a power loss) continues where it stopped.
# Usage: scripts/gpu_pipeline.sh            (idempotent; safe to call repeatedly)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=.venv/bin/python
MARK=var/pipeline
LOG=.runtime/logs/gpu_pipeline.log
mkdir -p "$MARK" .runtime/logs
exec >>"$LOG" 2>&1
exec 9>"$MARK/.lock"
flock -n 9 || { echo "$(date -Is) another pipeline instance is running"; exit 0; }

say() { echo "$(date -Is) == $*"; }
step() {   # step <name> <command...>
  local name=$1; shift
  if [[ -f "$MARK/$name.done" ]]; then say "skip $name (done)"; return 0; fi
  say "start $name"
  if "$@"; then touch "$MARK/$name.done"; say "done $name"; else say "FAILED $name (rc=$?)"; return 1; fi
}

say "pipeline start (uptime $(cut -d' ' -f1 /proc/uptime)s)"
pgrep -f "scripts/power_monitor.py" >/dev/null || setsid nohup $PY scripts/power_monitor.py >/dev/null 2>&1 < /dev/null 9>&- &
# Both tiers stay resident (ZRT or project vLLM, see scripts/runtime.sh); benches use at most 2 concurrent generations.
scripts/runtime.sh start 9>&- || exit 1
sleep 3

step router_data_local   $PY bench/generate_router_data.py --tier local --n-mmlu 900 --n-gsm8k 450 --n-kev 450 --concurrency 6 || exit 1
step router_data_large   $PY bench/generate_router_data.py --tier local_large --n-mmlu 900 --n-gsm8k 450 --n-kev 450 --concurrency 4 --only-split test || exit 1
step train_router        bash -c "cd bench && ../$PY train_router.py" || exit 1
step gateway_reload      bash -c "scripts/gateway.sh restart 9>&- && $PY scripts/wait_ready.py" || exit 1
# each bench is followed by a validity gate (bench/validate_run.py): a run with a dead model is marked INVALID, not done
step attack_suite        bash -c "cd bench && ../$PY attack_suite.py && ../$PY validate_run.py security" || exit 1
step load_test           bash -c "cd bench && ../$PY load_test.py --levels 1,2,4,8 --per-level 16 --max-tokens 128 && ../$PY validate_run.py load" || exit 1
step end_to_end          bash -c "cd bench && ../$PY evaluate_end_to_end.py --n 60 && ../$PY validate_run.py e2e" || exit 1
step offline_test        $PY scripts/offline_test.py || exit 1
step report              bash -c "cd bench && ../$PY generate_report.py" || exit 1
say "PIPELINE COMPLETE"

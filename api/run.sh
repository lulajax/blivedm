#!/usr/bin/env bash
# API 侧进程管理：api 调度端 + event_projector 投影进程
# 用法: ./run.sh {start|stop|restart|status|logs} [api|projector|all]
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs pids

svc_mod() { case "$1" in
  api)       echo blivedm_api.main;;
  projector) echo blivedm_api.event_projector;; esac; }

start_one() {
  local n=$1 pf="pids/$n.pid"
  if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
    echo "$n 已在运行 (pid $(cat "$pf"))"; return; fi
  nohup uv run python -m "$(svc_mod "$n")" >> "logs/$n.log" 2>&1 &
  echo $! > "$pf"; echo "$n 已启动 (pid $(cat "$pf"))"
}
stop_one() {
  local n=$1 pf="pids/$n.pid"
  [ -f "$pf" ] || { echo "$n 未运行"; return; }
  kill "$(cat "$pf")" 2>/dev/null || true; rm -f "$pf"; echo "$n 已停止"
}
status_one() {
  local n=$1 pf="pids/$n.pid"
  if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
    echo "$n  运行中 (pid $(cat "$pf"))"; else echo "$n  已停止"; fi
}
targets() { [ "${1:-all}" = all ] && echo "api projector" || echo "$1"; }

case "${1:-}" in
  start)   for n in $(targets "${2:-all}"); do start_one  "$n"; done;;
  stop)    for n in $(targets "${2:-all}"); do stop_one   "$n"; done;;
  restart) for n in $(targets "${2:-all}"); do stop_one   "$n"; sleep 1; start_one "$n"; done;;
  status)  for n in $(targets "${2:-all}"); do status_one "$n"; done;;
  logs)    tail -n 200 -f logs/"${2:-api}".log;;
  *) echo "用法: $0 {start|stop|restart|status|logs} [api|projector|all]"; exit 1;;
esac

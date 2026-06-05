#!/usr/bin/env bash
# 采集客户端进程管理
# 用法: ./run.sh {start|stop|restart|status|logs}
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs pids
pf="pids/client.pid"

case "${1:-}" in
  start)
    if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
      echo "client 已在运行 (pid $(cat "$pf"))"; exit 0; fi
    nohup uv run python -m blivedm_client.main >> logs/client.log 2>&1 &
    echo $! > "$pf"; echo "client 已启动 (pid $(cat "$pf"))";;
  stop)
    [ -f "$pf" ] && kill "$(cat "$pf")" 2>/dev/null || true; rm -f "$pf"; echo "client 已停止";;
  restart) "$0" stop; sleep 1; "$0" start;;
  status)
    if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
      echo "client 运行中 (pid $(cat "$pf"))"; else echo "client 已停止"; fi;;
  logs) tail -n 200 -f logs/client.log;;
  *) echo "用法: $0 {start|stop|restart|status|logs}"; exit 1;;
esac

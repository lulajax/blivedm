#!/usr/bin/env bash
# 回滚到上一版本（install.sh 每次部署会把旧包留为 *.prev）
set -euo pipefail
cd "$(dirname "$0")"
ARTIFACT="blivedm-api.tar.gz"
[ -f "$ARTIFACT.prev" ] || { echo "无上一版本可回滚"; exit 1; }

echo "回滚到上一版本"
tar xzf "$ARTIFACT.prev"
chmod +x run.sh rollback.sh 2>/dev/null || true
# 交换当前/上一版标记，便于来回切
mv -f "$ARTIFACT" "$ARTIFACT.swap" 2>/dev/null || true
mv -f "$ARTIFACT.prev" "$ARTIFACT"
[ -f "$ARTIFACT.swap" ] && mv -f "$ARTIFACT.swap" "$ARTIFACT.prev"

uv sync
./run.sh restart all
echo "回滚完成"

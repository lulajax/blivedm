#!/bin/bash
# 部署到 tiktok-99: 放在 /app/blivedm-api/install.sh，在该目录执行 ./install.sh
# 通过 HTTP 从 OSS 公网地址拉包 -> 解压(保留 .env/.venv/logs/pids) -> uv sync -> 重启
set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="https://mifeng-wehub-pic.oss-cn-beijing.aliyuncs.com/tiktok/blivedm"
ARTIFACT="blivedm-api.tar.gz"

echo "[1/5] 下载 $BASE_URL/$ARTIFACT"
wget -q -O "$ARTIFACT.tmp" "$BASE_URL/$ARTIFACT"
[ -s "$ARTIFACT.tmp" ] || { echo "下载失败或为空"; rm -f "$ARTIFACT.tmp"; exit 1; }
[ -f "$ARTIFACT" ] && mv -f "$ARTIFACT" "$ARTIFACT.prev"   # 留旧包用于回滚
mv -f "$ARTIFACT.tmp" "$ARTIFACT"

echo "[2/5] 解压（不覆盖 .env / .venv / logs / pids）"
tar xzf "$ARTIFACT"
chmod +x run.sh rollback.sh 2>/dev/null || true

echo "[3/5] 检查 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo ">>> 首次部署：已生成 .env 模板。请填好 MYSQL_* 等配置后重新执行 ./install.sh"
  exit 1
fi

echo "[4/5] uv sync"
uv sync

echo "[5/5] 重启 api + projector"
./run.sh restart all
./run.sh status
echo "部署完成。管理页: http://<本机IP>:8000/"

#!/usr/bin/env bash
# 本地执行：把 api/ 和 client/ 打成 tar 包并上传到 OSS。
# 用法: ./deploy/upload-to-oss.sh [all|api|client]
set -euo pipefail

BUCKET="mifeng-wehub-pic"
OSS_PREFIX="tiktok/blivedm"
ROOT=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &>/dev/null && pwd )

pack_and_upload() {
  local dir="$1" pkg="$2" artifact="$3" out="/tmp/$3"
  echo "==> 打包 $dir -> $out"
  # COPYFILE_DISABLE + --no-mac-metadata: 不写 macOS xattr，避免服务器解压时的 provenance 告警
  COPYFILE_DISABLE=1 tar --no-mac-metadata -czf "$out" -C "$ROOT/$dir" \
    --exclude='*__pycache__*' --exclude='*.pyc' --exclude='*.pytest_cache*' \
    "$pkg" pyproject.toml uv.lock README.md run.sh rollback.sh .env.example
  echo "==> 上传 oss://$BUCKET/$OSS_PREFIX/$artifact"
  ossutil cp "$out" "oss://$BUCKET/$OSS_PREFIX/$artifact" --loglevel info -f
  echo "==> 完成 $artifact"
}

upload_installers() {
  echo "==> 上传 install 脚本"
  ossutil cp "$ROOT/deploy/install-api.sh"    "oss://$BUCKET/$OSS_PREFIX/install-api.sh"    --loglevel info -f
  ossutil cp "$ROOT/deploy/install-client.sh" "oss://$BUCKET/$OSS_PREFIX/install-client.sh" --loglevel info -f
}

TARGET="${1:-all}"
case "$TARGET" in
  all)    pack_and_upload api    blivedm_api    blivedm-api.tar.gz
          pack_and_upload client blivedm_client blivedm-client.tar.gz
          upload_installers;;
  api)    pack_and_upload api    blivedm_api    blivedm-api.tar.gz;;
  client) pack_and_upload client blivedm_client blivedm-client.tar.gz;;
  installers) upload_installers;;
  *) echo "用法: $0 [all|api|client|installers]"; exit 1;;
esac
echo "全部上传完成。到 tiktok-99 各服务目录执行 ./install.sh 部署。"

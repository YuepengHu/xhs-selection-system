#!/bin/bash
# 双击运行：启动销量监控和采集界面，并自动打开浏览器
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

PORT="${XHS_PORT:-8765}"

if ! command -v uv >/dev/null 2>&1; then
  echo "还没有安装，请先双击“安装.command”。"
  read -r -p "按回车键关闭..." _
  exit 1
fi

if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  echo "已经在运行了，直接打开浏览器。"
  open "http://127.0.0.1:$PORT"
  exit 0
fi

echo "== 启动中，请不要关闭这个窗口（关闭窗口会停止监控）=="
echo "浏览器地址：http://127.0.0.1:$PORT"
(sleep 3 && open "http://127.0.0.1:$PORT") &
exec caffeinate -i uv run uvicorn xhs_monitor.app:app --host 127.0.0.1 --port "$PORT"

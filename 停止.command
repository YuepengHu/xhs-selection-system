#!/bin/bash
# 双击运行：停止监控服务
PORT="${XHS_PORT:-8765}"
PIDS=$(lsof -ti "tcp:$PORT" 2>/dev/null || true)
if [ -z "$PIDS" ]; then
  echo "没有正在运行的监控服务。"
else
  # shellcheck disable=SC2086
  kill $PIDS
  echo "已停止。"
fi
read -r -p "按回车键关闭..." _ || true

#!/bin/bash
# 双击运行：检查并安装所需软件（只需第一次）
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

echo "== 小红书选品系统：安装 =="

if ! command -v brew >/dev/null 2>&1; then
  echo "没有找到 Homebrew（Mac 上的软件安装工具）。"
  echo "请先按教程第 1 步安装 Homebrew，然后再双击本文件。"
  read -r -p "按回车键关闭..." _
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "安装 uv ..."
  brew install uv
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "安装 adb（连接安卓手机用）..."
  brew install android-platform-tools
fi

if [ ! -d "/Applications/Google Chrome.app" ]; then
  echo "没有找到 Google Chrome。请先安装：https://www.google.cn/chrome/"
  read -r -p "按回车键关闭..." _
  exit 1
fi

echo "安装程序依赖 ..."
uv sync

echo
echo "安装完成。以后双击“启动.command”即可使用。"
read -r -p "按回车键关闭..." _

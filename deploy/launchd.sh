#!/bin/bash
# 用法: deploy/launchd.sh install|uninstall|status|print
set -euo pipefail

LABEL="com.yuepeng.xhs-monitor"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${XHS_PORT:-8765}"

render() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$DIR/.venv/bin/python</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>xhs_monitor.app:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/data/monitor.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/monitor.log</string>
</dict>
</plist>
EOF
}

cmd="${1:-}"
case "$cmd" in
  print)
    render
    ;;
  install)
    [ -x "$DIR/.venv/bin/python" ] || { echo "先在 $DIR 运行 uv sync" >&2; exit 1; }
    mkdir -p "$DIR/data" "$HOME/Library/LaunchAgents"
    render > "$PLIST"
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$PLIST"
    launchctl kickstart -k "gui/$UID/$LABEL"
    echo "已安装并启动: http://127.0.0.1:$PORT  日志: $DIR/data/monitor.log"
    ;;
  uninstall)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "已卸载"
    ;;
  status)
    launchctl print "gui/$UID/$LABEL" | grep -E 'state|pid|last exit' || echo "未运行"
    ;;
  *)
    echo "用法: $0 install|uninstall|status|print" >&2
    exit 2
    ;;
esac

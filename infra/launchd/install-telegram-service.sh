#!/usr/bin/env bash
# Runs the Mentor Telegram bot as a macOS login service (until it moves to AWS in Week 3).
# It starts at login, restarts if it stops, and logs to ~/Library/Logs/Mentor/telegram.log.
#
# Usage:  infra/launchd/install-telegram-service.sh            install or update, then start
#         infra/launchd/install-telegram-service.sh uninstall  stop and remove
set -euo pipefail

LABEL="com.mentor.telegram"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Mentor"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
# bootout returns before the old service has gone; bootstrapping too early fails with error 5.
for _ in $(seq 1 20); do
  launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done

if [ "${1:-}" = "uninstall" ]; then
  rm -f "$PLIST"
  echo "Removed $LABEL."
  exit 0
fi

[ -x "$REPO_ROOT/.venv/bin/mentor" ] || { echo "Run 'uv sync' in $REPO_ROOT first." >&2; exit 1; }
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO_ROOT/.venv/bin/mentor</string>
    <string>telegram</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <!-- Restart if it exits, e.g. when Postgres or Ollama aren't up yet after login. -->
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/telegram.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/telegram.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed and started $LABEL. Logs: $LOG_DIR/telegram.log"

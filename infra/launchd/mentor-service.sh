#!/usr/bin/env bash
# Runs a Mentor command as a macOS login service: it starts at login, restarts if it stops, and
# logs to ~/Library/Logs/Mentor/<name>.log.
#
#   standby   watches the cloud server and runs the Telegram bot on the Mac while it's down
#   telegram  the Telegram bot itself; the standby starts and stops it, deploy.sh stops it
#
# Usage:  infra/launchd/mentor-service.sh standby|telegram            install or update, then start
#         infra/launchd/mentor-service.sh standby|telegram uninstall  stop and remove
set -euo pipefail

NAME="${1:-}"
case "$NAME" in
  standby | telegram) ;;
  *) echo "Usage: $0 standby|telegram [uninstall]" >&2; exit 1 ;;
esac
LABEL="com.mentor.$NAME"
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

if [ "${2:-}" = "uninstall" ]; then
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
    <string>$NAME</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <!-- The standby needs gcloud, which isn't on launchd's default PATH. -->
    <key>PATH</key><string>$PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <!-- Restart if it exits, e.g. when Postgres or Ollama aren't up yet after login. -->
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/$NAME.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/$NAME.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed and started $LABEL. Logs: $LOG_DIR/$NAME.log"

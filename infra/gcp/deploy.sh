#!/usr/bin/env bash
# Builds Mentor on the Mac and runs it on the Google Cloud server (plan D20).
#
# Usage:  infra/gcp/deploy.sh                build, upload and (re)start Mentor
#         infra/gcp/deploy.sh secrets        copy the bot token, chat ID and project ID from .env
#         infra/gcp/deploy.sh google-token   copy the Google sign-in (again after each
#                                            `uv run mentor google-auth`)
#         infra/gcp/deploy.sh logs           follow the bot's log
set -euo pipefail
source "$(dirname "$0")/config.sh"

secrets() {
  local token chat key gemini
  token=$(env_value MENTOR_TELEGRAM_BOT_TOKEN)
  chat=$(env_value MENTOR_TELEGRAM_ALLOWED_CHAT_ID)
  # Optional: only needed if a role in models.cloud.yaml uses the Anthropic API or Gemini. Claude
  # on Vertex AI signs in with the server's Google identity instead.
  key=$(env_value MENTOR_ANTHROPIC_API_KEY)
  gemini=$(env_value MENTOR_GEMINI_API_KEY)
  [ -n "$token" ] && [ -n "$chat" ] ||
    die "Set MENTOR_TELEGRAM_BOT_TOKEN and MENTOR_TELEGRAM_ALLOWED_CHAT_ID in .env"
  if grep -q "provider: anthropic" "$REPO_ROOT/config/models.cloud.yaml" && [ -z "$key" ]; then
    die "config/models.cloud.yaml uses the Anthropic API: set MENTOR_ANTHROPIC_API_KEY in .env"
  fi
  # Sent over SSH stdin, so the values never appear in a command line. The database password
  # is made on the server the first time and kept after that.
  on_server sudo bash -s <<SCRIPT
set -euo pipefail
cd /opt/mentor
pw=\$(sed -n 's/^POSTGRES_PASSWORD=//p' .env 2>/dev/null || true)
[ -n "\$pw" ] || pw=\$(openssl rand -hex 24)
umask 077
cat > .env <<ENV
POSTGRES_PASSWORD=\$pw
MENTOR_DATABASE_URL=postgresql+psycopg://mentor:\$pw@postgres:5432/mentor
MENTOR_TELEGRAM_BOT_TOKEN=$token
MENTOR_TELEGRAM_ALLOWED_CHAT_ID=$chat
MENTOR_GCP_PROJECT=$PROJECT
MENTOR_ANTHROPIC_API_KEY=$key
MENTOR_GEMINI_API_KEY=$gemini
ENV
SCRIPT
  echo "Secrets updated on the server. Run infra/gcp/deploy.sh to restart Mentor with them."
}

google_token() {
  local token="$HOME/.config/mentor/google_token.json"
  [ -f "$token" ] || die "No Google sign-in yet. Run: uv run mentor google-auth"
  on_server "sudo tee /opt/mentor/secrets/google_token.json >/dev/null &&
    sudo chown 10001:10001 /opt/mentor/secrets/google_token.json &&
    sudo chmod 600 /opt/mentor/secrets/google_token.json" < "$token"
  echo "Google sign-in copied to the server."
}

deploy() {
  docker buildx build --platform linux/amd64 -t mentor:latest --load "$REPO_ROOT"
  on_server "test -f /opt/mentor/.env" || die "Run infra/gcp/deploy.sh secrets first."
  on_server "sudo tee /opt/mentor/docker-compose.yml >/dev/null" \
    < "$REPO_ROOT/infra/gcp/docker-compose.yml"
  echo "Uploading the image..."
  docker save mentor:latest | gzip -1 | on_server "gunzip | sudo docker load"

  # Telegram hands each message to one bot connection only, so the Mac stops answering, and
  # what it said while answering is copied over before the server's bot starts.
  if launchctl print "gui/$(id -u)/com.mentor.telegram" >/dev/null 2>&1; then
    "$REPO_ROOT/infra/launchd/mentor-service.sh" telegram uninstall
    on_server "cd /opt/mentor && sudo docker compose up -d postgres"
    "$REPO_ROOT/.venv/bin/mentor" sync || echo "Couldn't sync; the Mac's latest messages stay on the Mac."
    echo "Stopped the Mac's Telegram bot: the server answers from now on."
  fi

  on_server "cd /opt/mentor && sudo docker compose up -d --remove-orphans &&
    sudo docker image prune -f >/dev/null && sleep 5 && sudo docker compose ps &&
    sudo docker compose logs --tail 20 telegram"
}

case "${1:-deploy}" in
  deploy) deploy ;;
  secrets) secrets ;;
  google-token) google_token ;;
  logs) on_server "cd /opt/mentor && sudo docker compose logs -f --tail 100 telegram" ;;
  *) die "Usage: $0 [deploy|secrets|google-token|logs]" ;;
esac

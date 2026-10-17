# Mentor

A personal AI chief of staff and coach: daily brief, routines, tasks, meetings, travel and research, acting only within strict approval rules.

- Product blueprint: [MENTOR_AI_Agent_Plan.md](MENTOR_AI_Agent_Plan.md)
- Build plan, decisions and status: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- AWS permissions and setup: [aws-iam/](aws-iam/)

## Development

```sh
uv sync                     # Python 3.13 + dev tools
docker compose -f infra/docker-compose.yml up -d   # local Postgres + pgvector (OrbStack)
uv run alembic upgrade head # create or upgrade the database tables
uv run pre-commit install   # lint, type-check and secret checks on every commit
uv run pytest
```

## Talk to Mentor

Needs the local database (above) and Ollama running with Gemma 4 (`ollama serve`).

```sh
uv run mentor chat            # terminal chat; the conversation is remembered between runs
uv run mentor chat --new      # start a fresh conversation
uv run mentor telegram        # Telegram bot; needs MENTOR_TELEGRAM_BOT_TOKEN in .env (see .env.example)
```

Mentor keeps lasting facts you tell it (your CV, preferences, people, goals) in long-term memory, looks them up when needed, and forgets them when you ask. A conversation itself only reaches back about 6,000 tokens.

## Always-on server (Google Cloud, until AWS)

The Telegram bot runs on a free-tier Google Cloud server with Gemini, so it answers while the Mac sleeps (plan D20). Telegram allows one bot connection, so deploying stops the Mac's bot service. Set `MENTOR_GCP_PROJECT` in `.env` to your Google Cloud project ID first.

```sh
infra/gcp/create-server.sh      # once: server, network, daily disk snapshot, budget alert, Gemini API key (needs billing linked)
infra/gcp/deploy.sh secrets     # copy the bot token, chat ID and Gemini key from .env
infra/gcp/deploy.sh google-token  # copy the Google sign-in; repeat after each `uv run mentor google-auth`
infra/gcp/deploy.sh             # build on the Mac, upload, (re)start; run after every code change
infra/gcp/deploy.sh logs        # follow the bot's log
```

**If the server goes down, the Mac takes over** (plan D21). The standby checks the server's bot every minute; if it goes silent (about 4 minutes without a heartbeat) or the server stops, the Mac starts the same Telegram bot on the local model and says so in the chat. When the server is back, it copies the conversation over, hands back and says so. Conversations and memories sync every 5 minutes, so both know the same things. The Mac must be awake and online.

```sh
infra/launchd/mentor-service.sh standby   # once: run the standby at login (log: ~/Library/Logs/Mentor/standby.log)
uv run mentor sync                        # sync conversations and memories with the server now
```

## Contributing

1. Work on a branch and open a pull request. GitHub runs the checks (lint, types, tests with a real Postgres), and `main` takes a pull request once the checks pass and the owner approves it.
2. Use your own accounts in your own `.env` (copy `.env.example`; git never stores it):
   - **Telegram:** create your own bot with @BotFather. Never run Mentor with someone else's bot token: Telegram lets only one copy of a bot connect, so yours would knock theirs offline.
   - **Model:** Ollama with Gemma 4 on your machine (the default, free), or your own Gemini key with `MENTOR_MODELS_FILE=models.cloud.yaml`.
   - **Google (optional):** your own OAuth client in your own Google Cloud project, saved as `~/.config/mentor/google_client_secret.json`, then `uv run mentor google-auth`.
3. Run `uv run pre-commit install` once, so the same checks and a secret scan run before each commit.

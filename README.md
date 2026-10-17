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

## Always-on server (Google Cloud, until AWS)

The Telegram bot runs on a free-tier Google Cloud server with Gemini, so it answers while the Mac sleeps (plan D20). Telegram allows one bot connection, so deploying stops the Mac's bot service. Set `MENTOR_GCP_PROJECT` in `.env` to your Google Cloud project ID first.

```sh
infra/gcp/create-server.sh      # once: server, network, budget alert, Gemini API key (needs billing linked)
infra/gcp/deploy.sh secrets     # copy the bot token, chat ID and Gemini key from .env
infra/gcp/deploy.sh google-token  # copy the Google sign-in; repeat after each `uv run mentor google-auth`
infra/gcp/deploy.sh             # build on the Mac, upload, (re)start; run after every code change
infra/gcp/deploy.sh logs        # follow the bot's log
```

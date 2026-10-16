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

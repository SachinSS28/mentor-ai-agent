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

## Run Mentor on the Mac (OrbStack)

Mentor runs entirely on the Mac (plan D23): Postgres and the Telegram bot in OrbStack, Gemma 4 in
Ollama on the Mac itself so it keeps the GPU. It answers while the Mac is on and awake. With
OrbStack set to start at login and Ollama installed as a login service (`brew services start
ollama`), Mentor comes back by itself after a restart.

```sh
docker compose -f infra/docker-compose.yml up -d --build   # start Mentor, or update it after a code change
docker compose -f infra/docker-compose.yml logs -f telegram # follow the bot
```

## Talk to Mentor

From the terminal as well as Telegram; needs the database (above) and Ollama running with Gemma 4.

```sh
uv run mentor chat            # terminal chat; the conversation is remembered between runs
uv run mentor chat --new      # start a fresh conversation
uv run mentor telegram        # the bot outside OrbStack; needs MENTOR_TELEGRAM_BOT_TOKEN in .env (see .env.example)
```

**Job Desk:** Mentor reads your LinkedIn job-alert emails every 15 minutes, numbers each job (J12), scores it against your profile and the `job_search` section of `config/charter.yaml`, and sends a shortlist on Telegram at 08:00. Reply "save J12", "applied J12" or "cover letter for J12"; emails to recruiters are saved as Gmail drafts, never sent.

**Research & news:** every 6 hours Mentor collects new papers, journal articles, water-sector news and funding calls (sources in `config/briefing.yaml`), scores them against your CV profile (`config/profile.md`), and adds the best to the 08:00 message. Ask any time: "any new papers on riverbank filtration?"

**Dhamma practice:** the 08:00 message opens with a teaching from the Pali Canon and a practice for the day (paraphrases in `config/dhamma.yaml`), at 21:30 Mentor sends an evening reflection prompt, and full and new moon Uposatha days are announced the evening before and on the day. Times and switches are in the `practice` section of `config/charter.yaml`.

**Networking:** ask "who should I meet in Hong Kong working on leak detection?" and Mentor finds researchers and engineers at Hong Kong institutions publishing on it (from OpenAlex), each numbered like P12 with their latest paper, career record and a LinkedIn search link. Every Monday the 08:00 message suggests five new people. Reply "note for P12" for a connection note, and "invited P12" or "met P12" to keep track. Topics are in the `network` section of `config/charter.yaml`. Mentor never acts on LinkedIn; you connect.

**Web search:** Mentor searches the web and reads pages for anything beyond your own data: companies, events, news, people's public work pages. It uses Tavily's free plan (1,000 searches a month, no card), with the key in `.env` as `MENTOR_TAVILY_API_KEY`. It never reads LinkedIn pages.

**Tasks and reminders:** "remind me Friday 9am to email Prof. Chan" adds a task (numbered like T3) with a Telegram reminder; "done T3" closes it. Mentor can also put focus, writing or meditation blocks on your own Google Calendar; it never invites anyone.

**Your documents:** Mentor reads (never changes) one Google Drive folder, "Mentor" by default (`documents` in `config/charter.yaml`): CVs, publication lists, statements and past cover letters, so applications and outreach use your real material.

**Photos, scans and PDFs:** send Mentor a photo, screenshot, image, PDF or Word file on Telegram, with a question as the caption ("is this seminar worth my time?"). Gemma reads it on the Mac, scanned PDFs included (first 5 pages), and Mentor answers and offers the next step, such as a calendar block or reminder. Files over Telegram's 20 MB limit can go in the Drive folder instead.

**Research highlights:** on Mondays and Thursdays the 08:00 message adds the most-cited recent papers in your field and new papers in high-impact journals (both from the journals listed under `highlights` in `config/briefing.yaml`, on `research_topics` in `config/charter.yaml`). The daily news also covers water and climate disasters, corporate sustainability and government climate action.

**Activity journal:** once a minute the Mac records which app and window or browser tab is in front (no screenshots; idle time skipped; WhatsApp, WeChat, banking and other private apps and sites stored only as "(private)"; kept 7 days; nothing leaves the Mac). Mentor uses it in check-ins, checklists and the weekly review; ask "can you see my screen?" or "what did I work on today?", or say "pause watching for 2 hours". It runs as a login service: `infra/launchd/mentor-service.sh watch` (add `uninstall` to remove it). Private lists are under `activity` in `config/charter.yaml`.

**Deeper research:** "find papers on riverbank filtration and virus removal" searches scholarly papers with citations and impact, and Mentor reads the full text when it's open access. "Research how Hong Kong cuts water leakage" starts a background report: several searches, the best sources read, and a report with numbered sources sent here as a file in a few minutes. "Search my documents for ..." finds passages by meaning in your Drive folder and past reports. Paper summaries (TLDRs) come from Semantic Scholar; its free key, in `.env` as `MENTOR_SEMANTIC_SCHOLAR_API_KEY`, keeps them from being rate-limited.

**Check-ins:** every 2 hours from 10:00 to 20:00 Mentor sends a short check-in: today's priorities, how the work is going, or a step toward one of your goals, ending with a question you can simply answer. It stays quiet while you're already chatting. Times are under `checkins` in `config/charter.yaml`.

**Weekly review:** every Sunday at 19:00 Mentor sends the week's facts (tasks done and overdue, applications, networking, research) and a short coach's note with the top three for next week, measured against the goals in `config/profile.md`.

Mentor keeps lasting facts you tell it (your CV, preferences, people, goals) in long-term memory, looks them up when needed, and forgets them when you ask. A conversation itself only reaches back about 6,000 tokens.

## Always-on server (Google Cloud): paused

**Stopped since 19 Oct (plan D23)**; its disk is kept. It ran the bot on a free-tier Google Cloud server so Mentor answered while the Mac slept (plan D20). `config/models.cloud.yaml` now uses Claude via the Anthropic API (needs paid API credits and `MENTOR_ANTHROPIC_API_KEY`); switch it back to a Gemini or Vertex AI role first if you restart the server without credits. To bring it back: `gcloud compute instances start mentor --zone us-west1-b`, stop the Mac's bot (`docker compose -f infra/docker-compose.yml stop telegram`), then deploy. Set `MENTOR_GCP_PROJECT` in `.env` to your Google Cloud project ID first.

```sh
infra/gcp/create-server.sh      # once: server, network, daily disk snapshot, budget alert, Gemini API key (needs billing linked)
infra/gcp/deploy.sh secrets     # copy the bot token, chat ID, project ID and API keys from .env
infra/gcp/deploy.sh google-token  # copy the Google sign-in; repeat after each `uv run mentor google-auth`
infra/gcp/deploy.sh             # build on the Mac, upload, (re)start; run after every code change
infra/gcp/deploy.sh logs        # follow the bot's log
```

**Standby (retired 19 Oct, while the Mac is Mentor's only home):** when the server ran, the Mac could take over if it went down (plan D21). The standby checks the server's bot every minute; if it goes silent (about 4 minutes without a heartbeat) or the server stops, the Mac starts the same Telegram bot on the local model and says so in the chat. When the server is back, it copies the conversation over, hands back and says so. Conversations and memories sync every 5 minutes, so both know the same things. The Mac must be awake and online.

```sh
infra/launchd/mentor-service.sh standby   # once: run the standby at login (log: ~/Library/Logs/Mentor/standby.log)
uv run mentor sync                        # sync conversations and memories with the server now
```

## Contributing

1. Work on a branch and open a pull request. GitHub runs the checks (lint, types, tests with a real Postgres), and `main` takes a pull request once the checks pass and the owner approves it.
2. Use your own accounts in your own `.env` (copy `.env.example`; git never stores it):
   - **Telegram:** create your own bot with @BotFather. Never run Mentor with someone else's bot token: Telegram lets only one copy of a bot connect, so yours would knock theirs offline.
   - **Model:** Ollama with Gemma 4 on your machine (the default, free), or Claude with your own Anthropic API key and credits (`MENTOR_MODELS_FILE=models.cloud.yaml`).
   - **Google (optional):** your own OAuth client in your own Google Cloud project, saved as `~/.config/mentor/google_client_secret.json`, then `uv run mentor google-auth`.
3. Run `uv run pre-commit install` once, so the same checks and a secret scan run before each commit.

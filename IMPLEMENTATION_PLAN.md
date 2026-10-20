# MENTOR — Implementation Plan (v1)

Based on `MENTOR_AI_Agent_Plan.md` (blueprint v1.0, 15 Oct 2026). This file is the canonical plan. Every major decision is confirmed with you before it's built.

**Status (16 Oct 2026):** Phase 0 started. AWS access will come later, so steps **0.3, 0.3b and 0.4** (and the Bedrock part of 0.9) wait for it. Done so far: AWS CLI installed; `aws-iam/` policies + `setup_aws.sh` prepared and tested offline; **0.1 done** (git on `main`, `backend/mentor` package skeleton, uv + Python 3.13, ruff/mypy strict/pytest/pre-commit with secret checks, all passing; pushed to private repo `SachinSS28/mentor-ai-agent`). Installed: GitHub CLI, OrbStack, Ollama + Gemma 4 E4B (tested: about 50 tokens/sec on the M5 Pro). **0.2** charter drafted in `config/charter.yaml` (language, tone, rhythm, ₹8,000 budget, approvals); workspaces, goals, working hours, routines and places still need your answers. **0.8 database done**: local Postgres 17 + pgvector in Docker (OrbStack), 14-table schema with an Alembic migration, unit + integration tests passing; api/worker/sentinel containers are added as they're built. **0.9 done (local model)**: `uv run mentor chat` talks to a LangGraph agent on Gemma 4 E4B with Postgres-backed conversation memory and a read-only open-tasks tool (calendar tool waits for step 0.5); Telegram bot **live** (long polling from the Mac, answers only your chat ID; moves to AWS in Week 3). Ollama and the bot run as login services. **0.5 done (read-only)**: Calendar + Gmail tools on Google's API library (G3), sign-in in Testing mode (D17); email content is passed to the model as untrusted data. Maps/Routes need a billing account and wait for Phase 3. **Phase 0 acceptance met on the local model** (bot replies; "What's on my calendar today?" answered from Google Calendar); G1 still waits for AWS. Mentor's owner in the charter is **Sachin**, based in **Hong Kong** (Asia/Hong_Kong). Finding: Gemma 4 returns empty answers after tool calls if thinking is switched off, so roles keep the model's default. **17 Oct:** the bot moves to an always-on Google Cloud server with Gemini (D20) while AWS waits; the Mac bot service is retired once the server answers. Done the same day: the bot answers from the server, and the repo is public (personal details removed from its history) with checks on every pull request; `main` needs an approved pull request. Later that day (D21): Memory v1 core (step 1.6: remember, recall, forget; the CV you pasted on 16 Oct saved) and the Mac standby with sync; `/memory` and `/forget` commands, seeding from the charter and embeddings remain for step 1.6.

---

## Context

You want **Mentor**, a personal AI chief-of-staff and coach. It runs your day, routines, tasks, meetings, travel and research, and it acts only within strict approval rules. The blueprint gives the product vision and a 12-week roadmap. This plan turns it into buildable steps, adjusted to the decisions you made today. Some of those decisions differ from the blueprint (LangGraph instead of the Claude Agent SDK, AWS Bedrock models, AWS managed hosting, iPhone instead of Android).

The project folder holds only the blueprint (no code, no git repo yet). The dev machine is an **Apple M5 Pro, 24 GB RAM, macOS 27**. It already has Python 3.14, uv, Node 26 and Homebrew. Docker, Ollama, Postgres and Redis are not installed.

---

## Decisions locked (your answers, 15 Oct 2026)

| # | Decision | Choice | Change from blueprint |
|---|---|---|---|
| D1 | Plan depth | All 6 phases planned at equal depth | — |
| D2 | Hosting | Mac + Docker Compose in Weeks 1–3, then **AWS Mumbai managed services** (ECS Fargate + RDS Postgres/pgvector), **sized lean** to fit the budget; final fit decided at G7 | VPS → managed AWS |
| D3 | Tasks | **Mentor Postgres is the source of truth**; a Notion Tasks database mirrors it with two-way sync | Notion *or* Todoist → DB + Notion |
| D4 | Agent framework | **LangGraph (Python)** + a config-driven model router | Claude Agent SDK → LangGraph |
| D5 | Models | **Bedrock only, processing kept in India**: GPT-5.6 **Terra** (orchestrator/planning), GPT-5.6 **Luna** (fast/cheap), gpt-oss-120b for bulk work if available in Mumbai. **No SageMaker** | Claude → OpenAI-on-Bedrock |
| D6 | Mobile | **Native SwiftUI iPhone app**, iPhone 15 Pro or newer (Apple Foundation Models + MLX) | Android/Kotlin/AICore → iOS |
| D7 | Chat channel (MVP) | Telegram bot, locked to your chat ID only | as blueprint |
| D8 | Laptop | This Mac is "Mentor Desktop" (the blueprint's Windows paths become macOS paths) | Windows → macOS |
| D9 | Secrets vault | **AWS Secrets Manager** (the same code path on the Mac and on AWS) | Bitwarden/Doppler → AWS-native (confirm at G2) |
| D10 | Infrastructure as code | **Terraform** (≥ 1.10, from the HashiCorp Homebrew tap). State in an S3 bucket in Mumbai with native S3 locking. Every AWS resource after the one-time console setup is managed in `infra/terraform/` | Docker Compose on a VPS → Terraform-managed AWS |
| D11 | Version control (G0) | git + **private GitHub repo** (GitHub CLI, browser sign-in) | — |
| D12 | Local containers | **OrbStack** (drop-in `docker` / `docker compose`) | — |
| D13 | Model until AWS access | **Gemma 4 E4B via Ollama** on the Mac behind the model router; switches to Bedrock with one change in `config/models.yaml` | temporary |
| D14 | Charter (0.2) | Filled in through a **chat interview**, then written to `config/charter.yaml` for your review | — |
| D15 | Queue, locks, rate limits | **Postgres only** (no Redis/ElastiCache): APScheduler job store, advisory locks, LangGraph checkpoints all in one database | removes ElastiCache |
| D16 | Budget | **HK$500/month all-in** (about US$64: AWS + models), enforced by Sentinel and AWS Budgets. This is below the lean managed-AWS estimate on its own, so G7 will most likely choose **one EC2 box + Docker Compose**; the managed stack stays the target only if Infracost shows it fits | tighter than the plan's estimate |
| D17 | Google sign-in | Google Cloud project on Sachin's personal Google account (its ID is `MENTOR_GCP_PROJECT` in `.env`), OAuth app in **Testing mode**: Google ends the sign-in every 7 days and Mentor says so; re-run `uv run mentor google-auth`. Publish properly in Phase 5, when Mentor has its own domain for the home page + privacy policy | temporary |
| D18 | Mac services | Ollama and the Telegram bot run as **login services** (`sh.brew.ollama`, `com.mentor.telegram`, installed by `infra/launchd/mentor-service.sh telegram`); OrbStack must have "Start at login" on | until AWS (Week 3) |
| D19 | Owner and coverage | Owner **Sachin, Hong Kong** (Asia/Hong_Kong). Local and travel features cover **Hong Kong** daily life (MTR/bus, HK Observatory typhoon and rainstorm signals, HKD, HK holidays), **Mumbai** trips (local trains, monsoon, Indian Railways) and **mainland China** travel (high-speed rail, flights). No Indian GST. AWS region stays Mumbai for GPT-5.6 (India in-country processing); G1 also checks Singapore | blueprint was Mumbai-only |
| D20 | Always-on host until AWS | Decided 17 Oct (the bot stopped whenever the Mac slept, and AWS access isn't ready): the Telegram bot + Postgres run on a **Google Cloud free-tier e2-micro** (us-west1, 30 GB standard disk, Docker Compose, no open ports, SSH only via IAP; `infra/gcp/`). Its model is **Gemini** (`config/models.cloud.yaml`) on a **free-tier AI Studio key** (changed 17 Oct, your choice: a paid key needs prepaid AI Studio credit), so **Google may use the prompts, including email and calendar content, to improve its products**, and free-tier rate limits apply; the server is billed to a billing account on Sachin's personal Google account: expected **about US$4/month** (the ~US$3.65 public IPv4 address). The Gemini subscription (on a university Workspace account) doesn't give the Developer Program Cloud credit, which is for personal @gmail.com accounts, and Mentor's billing stays off an account the university controls. Budget alert at 50/80/100%; a daily disk snapshot kept 7 days is the database's backup. The Mac keeps Gemma via Ollama for local development. Each message sends at most ~6,000 tokens of earlier conversation to the model | temporary; **data is processed in the US, not India (D5)**, until the AWS move |
| D21 | Memory and Mac standby | Decided 17 Oct (the cloud bot didn't know what you told the Mac bot, and the cloud server is a single point of failure): **Memory v1 core (step 1.6) brought forward**: `remember`, `recall_memories` and `forget_memory` tools on the `memories` table (word match until embeddings, G1; forgetting marks the row so it spreads; private memories reach only a local model and never sync). The Mac runs a **standby** (`mentor standby`, login service) that takes over the **same** Telegram bot when the cloud bot's heartbeat stops (about 4 minutes without a heartbeat, 2 checks with the server stopped, 10 minutes unreachable; never while the Mac itself is offline) and hands back when it returns, saying so in the chat. Telegram threads and memories sync both ways every 5 minutes through an SSH tunnel (conversation rows are only added and a thread's newest checkpoint wins; the newer memory edit wins). Around a switch a reply can be missed or doubled. The Mac must be awake | until AWS |
| D22 | Cloud model | Decided 19 Oct (Gemini's free tier allows 20 requests a day, about 5–10 messages, and kept running out): the cloud server's brain is **Claude Opus 5.5** via the **Anthropic API** (`config/models.cloud.yaml`, `MENTOR_ANTHROPIC_API_KEY`, key from Sachin's own Anthropic account); `fast` role = Claude Haiku 5.5. A **Vertex AI** route (Claude billed to the Google Cloud account; the server signs in as service account `mentor-server`, role Vertex AI User only) is built and tested, but Google auto-denied Claude quota to the new billing account on 19 Oct; switch `provider: vertex` back on if it's granted later. Cost controls: effort `low`, automatic prompt caching, the 6,000-token history window, a 2-minute timeout, and a monthly spend limit in the Anthropic console. Estimated US$0.04–0.12 per message, so the HK$500 budget holds only at moderate use. The system prompt no longer contains the time (each message is stamped instead) so it stays cacheable and earlier turns stay unchanged; stale thinking blocks are dropped (`prefix_mismatch_behavior: drop_block`), refusals fall back server-side, and the Mac's Gemma gets Claude's turns as plain text | Gemini free tier → Claude |
| D23 | Where Mentor runs | Decided 19 Oct (Sachin chose not to pay for Claude API credits; a Claude subscription can't serve a program's API calls): Mentor runs **entirely on the Mac**: Postgres + the Telegram bot in **OrbStack** (`infra/docker-compose.yml`, restarts with OrbStack at login), **Gemma 4 E4B** in Ollama on the Mac (login service, keeps the GPU; the bot reaches it at `host.docker.internal`). Answers only while the Mac is on and awake. The Google Cloud server is **stopped, disk kept** (restartable); the Mac standby is retired. The Claude code paths (D22: Anthropic API and Vertex AI) stay in place for later | supersedes D20 hosting and D22 for now |
| D24 | Mac brain | Decided 19 Oct after a head-to-head on Mentor's real prompt and tools: **Gemma 4 E4B stays** (personal, correct, 2–27 s) over Qwen2.5-14B-Instruct on MLX (older generation; misread a multi-day event, generic coaching, up to 90 s). Two fixes found on the way: Ollama ran Gemma with a **4,096-token** context, cutting off Mentor's instructions once memories were recalled (now `num_ctx: 32768` in `config/models.yaml`), and Gemma sometimes sends `query: []` to `recall_memories` (the tool now accepts a list) | — |
| D25 | Job search (LinkedIn) | Decided 19 Oct: a **Job Desk built from the LinkedIn job-alert emails** in Gmail (LinkedIn's API offers individuals no job search, and automating the LinkedIn site breaks its User Agreement and risks the account, so neither is used). Every 15 minutes the bot reads new alerts (`backend/mentor/jobs/`, three alert layouts), stores each job once (`jobs`, numbered J1, J2…), and Gemma scores it 0–100 against the saved profile and `job_search` in the charter. A shortlist of the best 8 (fit ≥ 50) goes to Telegram at **08:00 Hong Kong time** (or when the Mac wakes), with follow-ups due 7 days after applying. Tools: `find_jobs`, `update_job`, `check_job_alerts`, `draft_email` (Gmail drafts only, never sent; adds the `gmail.compose` scope). Cover letters and CV points come from the model with the job and the saved CV; applying stays one tap on LinkedIn | — |
| D26 | Profile and research briefing | Decided 19 Oct: Sachin's CV condensed into **`config/profile.md`** (specializations, research, career, goals; no contact details, membership numbers or referees, since the file is in the repo), included in Mentor's instructions and the job scoring. A **research & news briefing** (`backend/mentor/briefing/`, sources in `config/briefing.yaml`): every 6 hours new papers (OpenAlex, 15 phrases across 5 topics: water distribution & smart utilities, wastewater & GHG, groundwater & riverbank filtration, AI/ML in water, climate & sustainability), journal articles (Water Research, J. Hydrology, Water Research X, J. Environ. Management, Nature Water, ASCE JWRPM RSS), news (Google News feeds, HKSAR press releases filtered by keyword) and funding calls/conferences (WikiCFP, Google News; past-year events dropped); Gemma scores relevance to the profile; the best (≥ 60, up to 4/3/3/2 per section) join the **08:00 message** with the job shortlist (`backend/mentor/morning.py`). Tool: `find_research` | — |
| D27 | Dhamma practice | Decided 19 Oct: Sachin follows **Theravada** and trains toward Nibbāna alongside career and family; `config/persona.md` ("Path and values") and `config/profile.md` say so, and Mentor encourages with the Eightfold Path, precepts, meditation and the Brahmavihāras, warm and never preachy, never inventing quotations. Teachings come from **`config/dhamma.yaml`** (39 paraphrases with Pali Canon sources, 10 evening prompts), not from the model. The **08:00 message opens with the day's teaching and a practice**; a **21:30 evening reflection** prompt follows; **Uposatha** (full and new moon, Hong Kong dates from the Meeus moon-phase algorithm, `backend/mentor/practice/`) is announced the evening before and on the day. Settings in the `practice` section of `config/charter.yaml`. Reflections are not saved to memory unless asked | — |
| D28 | Networking | Decided 20 Oct: Mentor helps Sachin meet people in Hong Kong's water sector. **`find_experts`** searches OpenAlex (free, public publication records) for researchers and engineers at Hong Kong institutions publishing on the `network.topics` phrases in `config/charter.yaml` (exact phrases; papers from the last 3 years), with their latest paper, h-index and any career outside Hong Kong taken from their affiliation record, never guessed from names. Contacts live in the `people` table with a **P-number** and an outreach status (suggested, to_contact, invited, connected, replied, met, not_now); tools `track_contact` and `list_contacts`. **Mondays' 08:00 message suggests 5 new people**, and pending connection requests are checked on weekly. Mentor never touches LinkedIn (its terms forbid automation, and Sachin's account is at stake): it gives a LinkedIn people-search link and drafts a connection note, and Sachin connects. The persona now forbids promising background work ("I'll keep searching and report back"). Web search (Brave/Tavily) is a later option, needing Sachin's sign-up | — |
| D29 | Web search | Decided 20 Oct (settles the search part of G10): **Tavily**, free plan of 1,000 credits a month with no card, so it can never bill (past the allowance it refuses until the 1st); Brave now needs a card and bills overage without a cap. Tools **`search_web`** (basic search, 1 credit; `news` and `recent` options) and **`read_webpage`** (Tavily extract, 1 credit per 5 pages, most relevant parts for a question, cut to 6,000 characters) in `backend/mentor/mcp_servers/web_tools/`. Web text reaches the model wrapped as untrusted. LinkedIn pages are never read. Key `MENTOR_TAVILY_API_KEY` in the gitignored `.env` | — |
| D30 | Coaching toolkit | Decided 20 Oct, Gemma E4B kept as the brain: **tasks with T-numbers and Telegram reminders** (`add_task`, `complete_task`; a 1-minute reminder job), **calendar blocks** on Sachin's own calendar that never invite anyone (`add_calendar_block`, scope `calendar.events`), **read-only Drive** limited to one folder (`documents.drive_folder`, default "Mentor"; Google Docs, PDF, Word, text; `list_documents`, `read_document`; scope `drive.readonly`), a **Sunday 19:00 weekly review** (facts from Mentor's records plus a coach's note against the goals in the profile, `backend/mentor/review.py`), **research highlights on Mondays and Thursdays** (OpenAlex: most-cited papers of the last 24 months and new papers, both from 15 high-impact journals by two-year mean citedness, on the charter's `research_topics`; `backend/mentor/briefing/highlights.py`), and **wider daily news** (water and climate disasters, CSR/ESG, government climate action; 5 news items). The persona was rewritten to say what Mentor does instead of what it can't. Topics moved to `research_topics` in the charter (shared by networking and highlights) | — |
| D31 | Photos, scans and PDFs | Decided 20 Oct: Gemma 4 E4B reads images itself (Ollama lists vision; a poster was transcribed exactly in about 2-7 s), so nothing leaves the Mac. Telegram now takes photos, image files, PDFs and Word files (`make_media_handler`): each is read into text (`backend/mentor/reading.py`) and passed into the conversation as untrusted data with the caption, so history stays text. PDFs with a text layer are read directly (pypdf); scanned PDFs have their first 5 pages rendered (pypdfium2, 144 dpi) and read as images; GIF/BMP/TIFF are converted to PNG (Pillow). The Drive folder reads images and scans the same way. Mentor answers and offers the next step; it adds tasks or calendar blocks only when asked. A code check in the agent loop rewrites any reply that claims an action ("I've added...") when no changing tool ran in that turn | — |
| D32 | Check-ins | Decided 20 Oct at Sachin's request: a short check-in **every 2 hours from 10:00 to 20:00** (`checkins` in the charter; `backend/mentor/checkin.py`, a 1-minute job). It rotates between today's priorities (overdue and due tasks, the rest of the calendar), progress with one concrete offer of help, and one step toward a goal in the profile, at most 70 words and ending with a question. Skipped (not sent later) when Sachin wrote in the last 45 minutes, or when the Mac slept through the slot by more than an hour. A reply that claims an action falls back to the plain facts | — |

---

## Decision gates (I'll ask before building each step)

| Gate | When | Question | My default recommendation |
|---|---|---|---|
| **G0** | Before Phase 0 | `git init` the folder? Host on GitHub (private)? | ✅ Decided 16 Oct: git + private GitHub (D11) |
| **G1** | End of Phase 0 spike | Confirm final Bedrock model IDs, prices, embeddings model, gpt-oss availability, integration path | Terra/Luna via India profile; Cohere Embed Multilingual (Hindi support) if in Mumbai, else Titan v2 |
| **G2** | Phase 0 | Secrets: AWS Secrets Manager (~$0.40/secret/mo) vs SSM Parameter Store (free tier) | Secrets Manager for OAuth tokens, SSM for plain config |
| **G3** | Phase 1 | Google connectors: own thin Python tools vs official Workspace MCP (Developer Preview) vs community servers | ✅ Decided 16 Oct: own thin tools on Google's official API library, on Sachin's personal Google account |
| **G4** | Phase 1 | Sentinel: custom FastMCP gateway vs ToolHive + policy layer | ✅ Decided 16 Oct: custom FastMCP gateway |
| **G5** | Phase 1 | Voice-note speech-to-text | ✅ Decided 16 Oct: local Whisper (large-v3-turbo) on the Mac now; Amazon Transcribe once Mentor runs on AWS |
| **G6** | Phase 1 | Tracing | ✅ Decided 16 Oct: Sentinel audit log + structured logs only (no Langfuse); revisit if debugging needs it |
| **G7** | Week 3, before the first `terraform apply` of the managed stack | **Monthly AWS cost sign-off**, from an Infracost estimate of the `terraform plan`; keep or drop the Mac local-dev trust statement | I show the plan summary + monthly cost first; nothing billable is applied without your yes |
| **G8** | Phase 2 | Mac meeting recorder: Swift menu-bar app vs Python CLI + BlackHole | Swift menu-bar app (reuses Swift skills for the iPhone app) |
| **G9** | Phase 3 | Train MCP (after code review), flights provider (check that the Amadeus API is still available), receipt OCR (Textract AnalyzeExpense vs GPT-5.6 vision) | Decide after a 1-day spike |
| **G10** | Phase 4 | Search APIs (Brave/Tavily/Exa); SearXNG hosting (Mac vs AWS); research note home (Notion vs Drive) | Brave + Jina Reader; SearXNG on the Mac; Notion |
| **G11** | Phase 5 | App sign-in; Apple Developer Program ($99/yr); on-device Hindi path; on-device speech engine | Sign in with Apple + JWT; enrol; WhisperKit; Gemma 4 E2B via MLX for Hindi if quality passes |
| **G12** | Phase 6 | Bedrock Guardrails (extra cost) on top of the custom injection checks? | Decide based on injection-test results |

---

## Target architecture (as decided)

```
 iPhone app (SwiftUI, Phase 5)        Telegram bot (MVP)        Mac "Mentor Desktop"
 Foundation Models / MLX Gemma        locked to your chat ID     Ollama Gemma 4 E4B, whisper.cpp
          \                                   |                         /
           +--------------- AWS ap-south-1 (Mumbai) ------------------+
           |  ECS Fargate:  api  |  worker (scheduler, Notion sync)    |
           |                agent (LangGraph orchestrator + subgraphs)|
           |                sentinel (MCP gateway, policy, approvals, |
           |                          audit, Secrets Manager client)  |
           |  RDS Postgres + pgvector (data, checkpoints, job queue,  |
           |  locks)                     |  S3 (trip packs, receipts) |
           +--------------------------+-------------------------------+
                                      | every tool call goes through Sentinel
        mentor-core MCP (own) | google-tools MCP (own) | Maps | Weather | Notion
        Trains | Flights | Search/Reader | Sheets       Bedrock: GPT-5.6 Terra/Luna (India)
```

**Key design choices inside this architecture**
- **Sentinel is a separate service.** The agent connects to *one* MCP endpoint (Sentinel). Sentinel proxies to the upstream MCP servers and holds every credential, so the agent container has no Google, Notion or other tokens.
- **T2 approvals use "execute-on-approve".** Sentinel stores the exact action payload and its hash, and the graph pauses with LangGraph `interrupt()`. When you tap Approve, Sentinel runs *the stored payload*, not a fresh call from the agent. What you approved is exactly what runs.
- **Internal data goes through Sentinel too.** Tasks, memory and people are reached via an own `mentor-core` MCP server, so the audit log is complete ("What did Mentor do yesterday?").
- **Notion sync is plain code with no LLM.** A worker job uses the Notion API directly. That keeps it deterministic and cheap.
- **Telegram uses long polling in Phases 1–4.** No public endpoint, no load balancer and no domain are needed until the iPhone app arrives in Phase 5.

### Repo layout (monorepo)
```
mentor/
  backend/mentor/             # one Python 3.13 package (uv), so names never clash with libraries
    agent/                    # LangGraph: orchestrator, subgraphs, prompts, models.py (router)
    sentinel/                 # gateway, policy engine, approvals, audit, vault client
    mcp_servers/mentor_core/  # tasks, routines, goals, people, places, memory, workspaces
    mcp_servers/google_tools/ # calendar, gmail, drive, sheets (if G3 = own tools)
    channels/telegram/        # bot, inline approval buttons, voice notes
    api/                      # FastAPI: health, app REST + sync (Phase 5)
    worker/                   # APScheduler jobs: briefs, reviews, reminders, Notion sync
    db/                       # SQLAlchemy models + Alembic migrations
  config/  charter.yaml  policy.yaml  models.yaml  mcp_servers.yaml  persona.md
  infra/
    docker-compose.yml        # local stack on the Mac (Weeks 1–3, and dev afterwards)
    terraform/
      bootstrap/              # S3 state bucket (local state, run once)
      modules/                # network, data (RDS, S3), iam, secrets, ecs (cluster, ECR,
                              # services), observability (CloudWatch, Budgets), edge (Phase 5), github_oidc (Phase 6)
      envs/prod/              # main.tf, variables.tf, backend.tf, terraform.tfvars (no secrets)
      policies/               # the IAM JSON from aws-iam/, rendered with templatefile()
  desktop/ MentorDesktop (Swift menu-bar app, Phase 2)
  ios/     Mentor (SwiftUI app, Phase 5)
  tests/   unit/  integration/  injection_pack/  evals/
```

### Terraform conventions (apply in every phase)
- **One source of truth:** after the one-time console setup (Identity Center, root MFA), every AWS resource lives in Terraform. No console clicks for changes.
- **State:** S3 bucket `mentor-tfstate-ACCOUNT_ID` in ap-south-1, with versioning, SSE-KMS (`alias/mentor`), all public access blocked, and `use_lockfile = true` (native S3 locking, no DynamoDB table).
- **Secrets never enter Terraform:** Terraform creates empty `aws_secretsmanager_secret` containers only, and values are set with `aws secretsmanager put-secret-value`. RDS uses `manage_master_user_password = true`, so the DB password lives in Secrets Manager, never in state.
- **IAM policies come from the same JSON files** (`aws-iam/` → `infra/terraform/policies/`), loaded with `templatefile()`. Roles created earlier by `setup_aws.sh` are adopted with `import` blocks, not recreated.
- **Workflow:** `terraform fmt -check` → `validate` → `tflint` → `trivy config` (security scan) → `plan -out=tfplan` → I show you the summary and Infracost cost change → **you approve** → `apply tfplan`. Tags `project=mentor`, `env=prod` go on every resource via provider `default_tags`.
- **Pinned versions:** `required_version` and the AWS provider version are pinned; `.terraform.lock.hcl` is committed.

---

## Phase 0 — Foundations (Week 1: Mon 19 – Sun 25 Oct)

| Step | Work | Output |
|---|---|---|
| 0.1 | (G0) git init, monorepo skeleton, uv + Python 3.13 pinned, ruff/mypy/pre-commit, `.gitignore` that blocks secrets | Repo builds, lint passes |
| 0.2 | **Mentor charter**: I draft a template and interview you. Covers goals, routines, working hours, workspaces (one per business), home/office/client places, approval preferences, monthly budget cap in INR | `config/charter.yaml` |
| 0.3 | **AWS account**: one-time console steps (root MFA, IAM Identity Center + admin user), then `aws-iam/setup_aws.sh` (CLI profiles, KMS key, IAM roles, MentorDeveloper permission set); default region ap-south-1; Bedrock model access requested | Secure account; Mac signs in via SSO |
| 0.3b | **Terraform foundation**: install Terraform (≥ 1.10, `hashicorp/tap`), tflint, trivy, Infracost. Run `infra/terraform/bootstrap` to create the state bucket. `envs/prod` skeleton with the S3 backend and pinned provider, managing only cheap Phase 0 resources: AWS Budgets alerts at 50/80/100% of the cap, empty Secrets Manager containers (Telegram, Google OAuth, Notion, Maps). `import` blocks adopt the KMS key and IAM roles from `setup_aws.sh` | `terraform plan` shows **no changes** after import |
| 0.4 | **Bedrock spike** (`scripts/spikes/bedrock_check.py`): call Terra and Luna through the India profile from LangChain. Test tool calling, structured output, streaming and latency. Measure the token cost of one sample morning brief. Check gpt-oss + embeddings in Mumbai. Compare the two integration paths (`langchain-aws` Converse vs `langchain-openai` against Bedrock's OpenAI-compatible endpoint) | Spike report → **G1** |
| 0.5 | **Google Cloud project**: enable Calendar, Gmail, Drive, Sheets, Routes and Maps Grounding Lite. Set up the OAuth client and consent screen, and handle the "Testing mode = 7-day refresh token" issue (publish as an unverified production app limited to your own account) | OAuth works without weekly re-login |
| 0.6 | **Notion**: internal integration; Tasks database (Title, Status, Due, Priority, Workspace, Source, Mentor ID, Last synced) | Database shared with the integration |
| 0.7 | **Telegram**: create the bot in BotFather, capture your chat ID, allow-list only that ID | Bot token in Secrets Manager (G2) |
| 0.8 | **Local stack**: OrbStack (or Docker Desktop); `docker-compose.yml` with Postgres + pgvector (no Redis, D15); api, worker and sentinel are added as they're built; Langfuse after G6. Alembic initial migration with the Appendix C tables plus `workspaces`, `notion_sync`, `llm_usage` | `docker compose up` works |
| 0.9 | **Hello-world agent**: a LangGraph graph (Terra) with a Postgres checkpointer and one read-only tool (`calendar.list_events`); Telegram polling → graph → reply | You can chat with Mentor |
| 0.10 | **Local AI feel-test**: install Ollama + Gemma 4 E4B on the Mac (also try the 26B MoE; 24 GB RAM is tight). On the iPhone, try Apple Intelligence and an MLX-based local-model app | Notes for Phases 2 and 5 |

**Acceptance:** you message the bot and it replies via the local server. "What's on my calendar today?" gets a correct answer. The spike report is approved (G1). No secret is stored in the repo.

---

## Phase 1 — MVP "Chief of Staff" (Weeks 2–3: 26 Oct – 8 Nov)

**Week 2 — Sentinel + connectors + memory**

| Step | Work |
|---|---|
| 1.1 | **Sentinel v1** (G4): FastMCP gateway; upstream registry from `config/mcp_servers.yaml`; allow-list (new tools start disabled); policy engine from `config/policy.yaml` (T0–T3, conditions such as `attendees == [me]` and `new_recipient_always_ask`, rate limits, quiet hours); secrets injected only into upstream calls; audit log (tool, redacted args + hash, result summary, tier, approval_id, latency, cost); T3 hard block; `/pause` flag checked on every call |
| 1.2 | **Approvals**: T2 → approval record (exact payload + hash, preview text, 120-minute expiry) → graph `interrupt()` → Telegram inline **Approve / Edit / Reject** → `Command(resume=…)` → Sentinel runs the stored payload. Expired approvals send nothing |
| 1.3 | **Injection defence v1**: results from email, web, calendar and Notion are wrapped as `<untrusted source=…>` data. Before any T2 action, an **intent check** (Luna) compares the action with your original request in graph state and blocks on a mismatch |
| 1.4 | **Connectors**: `mentor-core` MCP (tasks, routines, people, places, workspaces, memory, reminders); `google-tools` (G3): calendar list / create hold (T1) / create with guests (T2), Gmail search/read/draft (send = T2, used from Phase 2); Maps travel time; Open-Meteo weather |
| 1.5 | **Model router**: `config/models.yaml` roles (orchestrator, fast, bulk, embed) → `mentor/agent/models.py` factory; a token-cost callback writes `llm_usage`; Sentinel enforces the monthly cap (at 100%: downgrade to Luna and block anything non-essential) |
| 1.6 | **Memory v1**: profile, preferences, workspaces and people seeded from the charter; a `memories` table with pgvector; "remember …" saves a fact and a correction overwrites the old one; `/memory` shows what is stored; `/forget <thing>` deletes it everywhere and confirms. Every record carries a `workspace_id` so businesses never mix |
| 1.7 | **Persona**: `config/persona.md` adapted from Appendix B (short, warm, honest; Hinglish when you use it) |

**Week 3 — Daily rhythm + Notion sync + AWS go-live**

| Step | Work |
|---|---|
| 1.8 | **Worker/scheduler** (APScheduler, single worker): morning brief (prepared at 06:45, sent at 07:00 IST), evening review (21:30), minute-level reminder scan, routine check-ins, Sunday-review stub. **Watchdog:** if the brief hasn't gone out by 07:05, send a plain template brief that needs no LLM |
| 1.9 | **Morning brief graph**: calendar + travel time between meetings, top 3 priorities, overdue tasks, weather/rain alert, follow-ups and birthdays, one mentor prompt |
| 1.10 | **Task capture**: text and Telegram voice notes (STT per G5) → Luna extracts title, due date, priority and workspace → task created (T1 + notify) → optional calendar block proposal |
| 1.11 | **Notion two-way sync**: outbox table pushes DB changes to Notion; Notion edits are pulled every 3 minutes (or via webhooks) using `last_edited_time`; per-field last-write-wins; a `sync_version` prevents echo loops; nightly full reconcile |
| 1.12 | **Commands**: `/brief /today /tasks /pause /resume /memory /forget /audit` |
| 1.13 | **AWS go-live with Terraform** (G7, target **Wed 4 Nov**): Terraform modules for `network` (VPC, **no NAT Gateway**; public subnets with locked-down security groups or VPC endpoints), `data` (RDS Postgres + pgvector: small Graviton, single-AZ, encrypted, 7-day backups, RDS-managed password; S3 `mentor-files` bucket), `ecs` (ECR repos, cluster, Fargate services agent+api, worker, sentinel internal-only via Service Connect), `observability` (CloudWatch log groups, alarms). `terraform plan` + Infracost → **G7 cost sign-off** → `terraform apply` from the Mac (CI comes in Phase 6). Container images are built locally and pushed to ECR with a `make deploy` script |

**Acceptance:** tasks captured from voice notes; Notion edits round-trip within 5 minutes; **zero T2 actions without approval** (shown by the audit log and automated tests); **7 on-time morning briefs in a row from AWS**. That streak starts 4 Nov and finishes during Week 4, overlapping Phase 2.

---

## Phase 2 — Meetings (Weeks 4–5: 9 – 22 Nov)

| Step | Work |
|---|---|
| 2.1 | **Scheduling**: free/busy across your calendars, each contact's time zone stored on `people`, proposes 3 slots, invite + Meet link = **T2** with an exact preview |
| 2.2 | **Pre-meeting brief** (30 minutes before): attendees from people memory, last Gmail threads, open tasks in that workspace, agenda (treated as untrusted), docs to open, leave-by time if in person |
| 2.3 | **Mentor Desktop recorder** (G8): menu-bar app that offers to record when a calendar event starts. It captures mic + system audio (Core Audio process tap / ScreenCaptureKit) and runs whisper.cpp large-v3-turbo on Metal. **Audio never leaves the Mac** (auto-deleted after N days). Only transcript text is sent, opt-in per meeting, with a consent reminder |
| 2.4 | **Meeting Secretary subgraph**: transcript → summary, decisions, action items with owners and dates → your tasks (T1), others' items as follow-ups → follow-up email draft (draft T1, **send T2**) → updates the meeting record and the people's last-contact date |
| 2.5 | **Offline/outage fallback**: the desktop app sends read-only questions to Ollama Gemma 4 E4B when Bedrock is unreachable |

**Acceptance:** 5 real meetings fully processed (brief → transcript → tasks → follow-up), each taking under 2 minutes of your effort.

---

## Phase 3 — Travel (Weeks 6–7: 23 Nov – 6 Dec)

| Step | Work |
|---|---|
| 3.1 | **Leave-by alerts**: `places` table; traffic/transit-aware ETA (Routes API) at T-90 and T-60; per-city buffer rules: Hong Kong (MTR/bus, +20 min under typhoon signal 3+ or amber/red/black rainstorm warnings from the HK Observatory), Mumbai (local train +15, monsoon/rain alert +20), rush hour everywhere; alert 10 minutes before leave-by |
| 3.2 | **Trains** (G9): Indian Railways for Mumbai trips (reviewed community MCP, version pinned, behind Sentinel: PNR watcher, live running status, booking hand-off where you complete IRCTC payment/OTP) and Hong Kong ↔ mainland high-speed rail (schedule lookup + booking hand-off; no booking API) |
| 3.3 | **Flights/hotels**: search + shortlist (provider per G9); booking only as a T2 hand-off |
| 3.4 | **Travel Desk subgraph**: trip record (legs, PNRs, hotel, contacts) → calendar holds → **trip pack** (JSON + PDF, stored encrypted in S3, sent as a PDF on Telegram until the iPhone app caches it) |
| 3.5 | **Expense capture**: bill photo via Telegram → OCR (G9) → vendor, amount, currency (HKD, INR, CNY) converted to HKD → `expenses` table + Google Sheet row (T1), linked to the trip. No Indian GST |

**Acceptance:** one complete trip run by Mentor (plan → bookings approved → alerts → expenses logged).

---

## Phase 4 — Research + Mentor mode (Week 8: 7 – 13 Dec)

| Step | Work |
|---|---|
| 4.1 | **Researcher subgraph** (G10): plan queries → search → read pages (all content untrusted) → bulk model reads, Terra synthesises with citations → Notion Research page (date, sources, 5-line summary) |
| 4.2 | **Private mode**: SearXNG + Gemma on the Mac, so sensitive topics never reach the cloud model |
| 4.3 | **Standing briefs**: watches defined in config (industry news, competitors, market watchlist), scheduled, with URL dedupe |
| 4.4 | **Coach**: goals hierarchy (quarter → month → week); commitment check (planned hours vs free calendar hours); slip detection (a task moved 3+ times); low-value meeting flags; **Sunday 19:00 weekly review** (wins, slips, goal progress, next week's plan, self-reported time saved → KPI); monthly stop/start/continue; learning plans → weekly calendar holds |

**Acceptance:** 3 research notes you'd forward to a client without edits; the first full Sunday review.

---

## Phase 5 — iPhone app + on-device intelligence (Weeks 9–11: 14 Dec – 3 Jan)

| Step | Work |
|---|---|
| 5.1 | **Backend prerequisites** (G11): public HTTPS API via a new Terraform `edge` module (domain in Route 53 + ACM certificate + ALB or API Gateway; the APNs key as an empty secret container); Sign in with Apple → JWT, allow-listed to your Apple ID only; incremental sync API (per-table cursors, idempotent operations); APNs push for briefs, reminders and approvals (needs the Apple Developer Program) |
| 5.2 | **App screens (SwiftUI, iOS 26+)**: Today (agenda, top 3, leave-by), Chat (text + voice), **Approvals inbox** (plus actionable push notifications with Face ID before T2), Tasks, Trip pack, Memory & Settings (persona, connectors on/off, data report) |
| 5.3 | **On-device router**: Apple **Foundation Models** (guided structured output) for intent detection, quick answers from the local cache, and PII redaction before anything is sent. Apple's model may not support Hindi, so Hindi/Hinglish uses **Gemma 4 E2B via MLX Swift** if it passes a quality test (G11), otherwise the cloud after on-device rule-based redaction. Items marked Private never leave the phone |
| 5.4 | **Speech**: on-device STT (WhisperKit / Apple SpeechAnalyzer / Sarvam Edge per G11); TTS through AVSpeechSynthesizer (en-IN, hi-IN voices) |
| 5.5 | **Local storage**: GRDB + SQLCipher, key held in Keychain/Secure Enclave. A **Private vault** (journal, reflections, ID documents) is phone-only, Face ID-gated, and excluded from sync and iCloud backup |
| 5.6 | **Offline outbox + sync**: queued operations with idempotency keys; sync on foreground and through BGAppRefreshTask. Reminders are scheduled as **local notifications** so they fire with no signal |
| 5.7 | **Device tools**: App Intents (Siri/Shortcuts: "Tell Mentor…"), EventKit calendar cache, Contacts lookup, CoreLocation geofences for leave-by, a share extension (screenshots/links → task), alarms/notifications |
| 5.8 | **Distribution**: TestFlight (internal) |

**Acceptance:** in airplane mode you can ask "What's my next meeting?", add a task by voice and open the trip pack, and everything syncs when you're back online. Approving from a push notification works end to end.

---

## Phase 6 — Hardening + go-live (Week 12: 4 – 10 Jan 2027)

| Step | Work |
|---|---|
| 6.1 | **Injection test pack**: 30+ poisoned samples (emails, calendar invites with hidden instructions, web pages, PDFs, Notion edits). Automated tests assert that no T2 action runs, the intent check fires and the new-recipient rule holds. Optional Bedrock Guardrails (G12) |
| 6.2 | **Evals**: a golden set of about 50 daily requests with the expected tool calls, re-run whenever a model or prompt changes |
| 6.3 | **Backup/restore drill**: restore an RDS snapshot to a fresh instance; S3 versioning; written runbook |
| 6.4 | **Cost controls**: per-task budgets, alerts at 50/80/100% (Sentinel + AWS Budgets), automatic downgrade to Luna at the cap |
| 6.5 | **Kill switch test**: `/pause` stops the scheduler and Sentinel blocks everything except T0 |
| 6.6 | **KPI dashboard**: brief on-time %, capture latency, action-item → task conversion, approval stats, monthly cost (CloudWatch dashboard or a small FastAPI page) |
| 6.7 | **CI/CD + security pass**: GitHub Actions with OIDC (no stored AWS keys). On every PR: tests, `terraform fmt/validate/tflint/trivy`, and a `terraform plan` + Infracost comment. On merge to `main`: image build → ECR push → `terraform apply` behind a manual-approval GitHub environment. Weekly drift check (`terraform plan -detailed-exitcode`) alerts on console changes. IAM least privilege, security-group review, secret rotation, pinned community MCP versions; 30-day review cadence |

**Acceptance:** all injection tests blocked; restore from backup works; KPIs reporting.

---

## Verification (how each phase is proven)

- **Unit tests** (`uv run pytest tests/unit`): policy engine (tier mapping, conditions, quiet hours, expiry, T3 block, budget cap), payload hashing, Notion sync conflict rules, GSTIN validation.
- **Integration tests** (`tests/integration`, run against `docker compose up`): Sentinel with fake upstream MCP servers; graph runs against recorded model responses; the full approval round trip (interrupt → approve → execute the stored payload).
- **Injection pack + evals** (`tests/injection_pack`, `tests/evals`): from Phase 1 onward, extended every phase.
- **Manual end-to-end scripts per phase**: Telegram conversations that check each acceptance item, cross-checked against the audit log and Langfuse/CloudWatch traces.
- **Infrastructure checks** (every Terraform change): `terraform fmt -check`, `terraform validate`, `tflint`, `trivy config infra/terraform` with no HIGH/CRITICAL findings, and a reviewed `terraform plan`. After apply, a second `terraform plan` must show **no changes**, then smoke tests run against AWS (`aws ecs describe-services` shows healthy services, the bot answers, `/audit` shows the call).
- **Phase gates**: we don't move on until a phase's acceptance checks pass (as the blueprint says).

---

## Rough monthly cost (to confirm at G7 with an Infracost estimate of the Terraform plan)

| Item | Estimate |
|---|---|
| AWS managed stack, Phases 1–4 (Fargate ×3 small tasks, smallest RDS, Secrets, CloudWatch; **no NAT, no ALB, no ElastiCache**) | roughly US$50–90; must fit the ₹8,000 all-in cap with models (checked at G7) |
| + ALB/API Gateway + domain from Phase 5 | +US$5–25 |
| Bedrock tokens (Terra/Luna/gpt-oss) | measured in the Phase 0 spike; enforced by the Sentinel cap |
| Apple Developer Program | US$99/year (Phase 5) |
| Google Maps, search APIs, Notion, Telegram | free tiers / existing plans |

---

## New risks from today's decisions

| Risk | Mitigation |
|---|---|
| Google OAuth "Testing" mode expires refresh tokens every 7 days | Publish the OAuth app (unverified, own account only) in Phase 0 |
| GPT-5.6 tool calling through Bedrock's India profile + LangChain may have quirks | Phase 0 spike before any agent code; the router lets us swap models |
| Managed AWS cost creep (NAT Gateway, ALB, oversized RDS) | No NAT; long polling until Phase 5; Budgets alerts; Infracost on every `terraform plan`; cost sign-off at G7 |
| Terraform state holds sensitive data, or a console change drifts from the code | Secret values never enter Terraform; RDS-managed password; state bucket encrypted, versioned, admin-only; weekly drift check (Phase 6) |
| `terraform apply` destroys or replaces a stateful resource by mistake | `prevent_destroy` on RDS, the state bucket, the KMS key and the files bucket; RDS deletion protection + final snapshot; every plan reviewed before apply |
| iOS background limits (no always-on service) | Server-driven APNs pushes + local notifications + background refresh |
| Apple's on-device model may lack Hindi | Gemma 4 E2B via MLX or cloud fallback (G11) |
| 12 weeks is aggressive for one builder, now including a Mac app and an iPhone app | Phase gates; Phase 5 can slip without blocking daily value from Phases 1–4 |

---

## Appendix A — IAM inline policies (draft, region ap-south-1)

**Placeholders to replace:** `ACCOUNT_ID`, `KMS_KEY_ID` (the customer-managed key for Mentor's secrets and S3). The Bedrock model IDs (`in.openai.gpt-5.6-terra/luna`, `openai.gpt-oss-120b-1:0`, `cohere.embed-multilingual-v3`) are **unverified until G1**. `aws-iam/setup_aws.sh` creates these roles for Weeks 1–3. From step 0.3b, **Terraform owns them**: it loads these same JSON files with `templatefile()` (placeholders become variables) and adopts the existing roles with `import` blocks.

**Naming conventions these policies assume**
- Secrets: `mentor/app/*` are injected into containers at start (DB user per service, Telegram token, APNs key). `mentor/connectors/*` are fetched at runtime by Sentinel only (Google OAuth, Notion, Maps, search keys).
- S3 bucket `mentor-files-ACCOUNT_ID` (prefixes `trip-packs/`, `receipts/`); ECR repos `mentor/*`; log groups `/mentor/*`.
- Separate Postgres users per service: the agent's user can reach only the LangGraph checkpoint schema; mentor-core (inside Sentinel) owns the app tables.

| Role | Used by | Can | Explicitly cannot |
|---|---|---|---|
| `mentor-ecs-execution` | ECS itself, at container start | pull images, write logs, inject `mentor/app/*` | anything at runtime |
| `mentor-agent-task` | agent + api + Telegram bot | Bedrock (Terra, Luna, gpt-oss, embeddings), Transcribe streaming (G5), metrics | **any `mentor/connectors/*` secret**; Bedrock outside India |
| `mentor-sentinel-task` | Sentinel + its MCP servers | read connector secrets, save refreshed Google tokens, Luna intent check, S3 trip packs/receipts, Textract (G9) | — |
| `mentor-worker-task` | scheduler, briefs, Notion sync | Bedrock (same as agent), Notion secret only, metrics | Bedrock outside India |
| `MentorDeveloper` (Identity Center permission set) | you on the Mac, Weeks 1–3 | assume the three task roles, so local containers run with their cloud permissions | — |

### A1. `mentor-ecs-execution`
```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "EcrAuth", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    {
      "Sid": "PullMentorImages",
      "Effect": "Allow",
      "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
      "Resource": "arn:aws:ecr:ap-south-1:ACCOUNT_ID:repository/mentor/*"
    },
    {
      "Sid": "WriteMentorLogs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:ap-south-1:ACCOUNT_ID:log-group:/mentor/*:*"
    },
    {
      "Sid": "InjectAppSecretsAtStart",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:mentor/app/*"
    },
    {
      "Sid": "DecryptAppSecrets",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "arn:aws:kms:ap-south-1:ACCOUNT_ID:key/KMS_KEY_ID",
      "Condition": { "StringEquals": { "kms:ViaService": "secretsmanager.ap-south-1.amazonaws.com" } }
    }
  ]
}
```

### A2. `mentor-agent-task`
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeGpt56ViaIndiaProfile",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-terra",
        "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-luna"
      ]
    },
    {
      "Sid": "Gpt56ModelsOnlyThroughIndiaProfile",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:ap-south-1::foundation-model/openai.gpt-5.6-*",
        "arn:aws:bedrock:ap-south-2::foundation-model/openai.gpt-5.6-*"
      ],
      "Condition": {
        "StringLike": {
          "bedrock:InferenceProfileArn": "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-*"
        }
      }
    },
    {
      "Sid": "InvokeBulkAndEmbeddingModelsInMumbai",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:ap-south-1::foundation-model/openai.gpt-oss-120b-1:0",
        "arn:aws:bedrock:ap-south-1::foundation-model/cohere.embed-multilingual-v3"
      ]
    },
    {
      "Sid": "OptionalG5TranscribeVoiceNotes",
      "Effect": "Allow",
      "Action": [
        "transcribe:StartStreamTranscription",
        "transcribe:StartStreamTranscriptionWebSocket"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "ap-south-1"
        }
      }
    },
    {
      "Sid": "PublishMentorMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "cloudwatch:namespace": "Mentor"
        }
      }
    },
    {
      "Sid": "OptionalG6Tracing",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    },
    {
      "Sid": "NeverReadConnectorCredentials",
      "Effect": "Deny",
      "Action": "secretsmanager:*",
      "Resource": "arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:mentor/connectors/*"
    },
    {
      "Sid": "KeepBedrockInIndia",
      "Effect": "Deny",
      "Action": "bedrock:*",
      "Resource": "*",
      "Condition": {
        "StringNotEquals": {
          "aws:RequestedRegion": [
            "ap-south-1",
            "ap-south-2"
          ]
        }
      }
    }
  ]
}
```

### A3. `mentor-sentinel-task`
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadConnectorCredentials",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:mentor/connectors/*"
    },
    {
      "Sid": "SaveRefreshedGoogleTokens",
      "Effect": "Allow",
      "Action": "secretsmanager:PutSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:mentor/connectors/google-oauth-*"
    },
    {
      "Sid": "UseMentorKeyForSecretsAndFiles",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "arn:aws:kms:ap-south-1:ACCOUNT_ID:key/KMS_KEY_ID",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": [
            "secretsmanager.ap-south-1.amazonaws.com",
            "s3.ap-south-1.amazonaws.com"
          ]
        }
      }
    },
    {
      "Sid": "IntentCheckWithLuna",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-luna"
    },
    {
      "Sid": "LunaModelOnlyThroughIndiaProfile",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": [
        "arn:aws:bedrock:ap-south-1::foundation-model/openai.gpt-5.6-luna*",
        "arn:aws:bedrock:ap-south-2::foundation-model/openai.gpt-5.6-luna*"
      ],
      "Condition": {
        "StringEquals": {
          "bedrock:InferenceProfileArn": "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-luna"
        }
      }
    },
    {
      "Sid": "TripPacksAndReceipts",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::mentor-files-ACCOUNT_ID/trip-packs/*",
        "arn:aws:s3:::mentor-files-ACCOUNT_ID/receipts/*"
      ]
    },
    {
      "Sid": "ListOnlyMentorPrefixes",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::mentor-files-ACCOUNT_ID",
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "trip-packs/*",
            "receipts/*"
          ]
        }
      }
    },
    {
      "Sid": "OptionalG9ReceiptOcr",
      "Effect": "Allow",
      "Action": "textract:AnalyzeExpense",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "ap-south-1"
        }
      }
    },
    {
      "Sid": "PublishMentorMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "cloudwatch:namespace": "Mentor"
        }
      }
    }
  ]
}
```

### A4. `mentor-worker-task`
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeGpt56ViaIndiaProfile",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-terra",
        "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-luna"
      ]
    },
    {
      "Sid": "Gpt56ModelsOnlyThroughIndiaProfile",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:ap-south-1::foundation-model/openai.gpt-5.6-*",
        "arn:aws:bedrock:ap-south-2::foundation-model/openai.gpt-5.6-*"
      ],
      "Condition": {
        "StringLike": {
          "bedrock:InferenceProfileArn": "arn:aws:bedrock:ap-south-1:ACCOUNT_ID:inference-profile/in.openai.gpt-5.6-*"
        }
      }
    },
    {
      "Sid": "InvokeBulkAndEmbeddingModelsInMumbai",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:ap-south-1::foundation-model/openai.gpt-oss-120b-1:0",
        "arn:aws:bedrock:ap-south-1::foundation-model/cohere.embed-multilingual-v3"
      ]
    },
    {
      "Sid": "ReadNotionTokenForSync",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:mentor/connectors/notion-*"
    },
    {
      "Sid": "DecryptNotionToken",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "arn:aws:kms:ap-south-1:ACCOUNT_ID:key/KMS_KEY_ID",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "secretsmanager.ap-south-1.amazonaws.com"
        }
      }
    },
    {
      "Sid": "PublishMentorMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "cloudwatch:namespace": "Mentor"
        }
      }
    },
    {
      "Sid": "KeepBedrockInIndia",
      "Effect": "Deny",
      "Action": "bedrock:*",
      "Resource": "*",
      "Condition": {
        "StringNotEquals": {
          "aws:RequestedRegion": [
            "ap-south-1",
            "ap-south-2"
          ]
        }
      }
    }
  ]
}
```

### A5. `MentorDeveloper` permission set (inline policy, Mac local dev)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RunLocalContainersAsTheirCloudRoles",
      "Effect": "Allow",
      "Action": ["sts:AssumeRole", "sts:TagSession"],
      "Resource": [
        "arn:aws:iam::ACCOUNT_ID:role/mentor-agent-task",
        "arn:aws:iam::ACCOUNT_ID:role/mentor-sentinel-task",
        "arn:aws:iam::ACCOUNT_ID:role/mentor-worker-task"
      ]
    }
  ]
}
```

### A6. Trust policy for the three task roles (not inline; set on each role)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcsTasksInThisAccount",
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "ArnLike": { "aws:SourceArn": "arn:aws:ecs:ap-south-1:ACCOUNT_ID:*" },
        "StringEquals": { "aws:SourceAccount": "ACCOUNT_ID" }
      }
    },
    {
      "Sid": "LocalDevFromMacWeeks1to3",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::ACCOUNT_ID:root" },
      "Action": ["sts:AssumeRole", "sts:TagSession"],
      "Condition": {
        "ArnLike": { "aws:PrincipalArn": "arn:aws:iam::ACCOUNT_ID:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_MentorDeveloper_*" }
      }
    }
  ]
}
```
The execution role uses the same trust policy minus the `LocalDevFromMacWeeks1to3` statement. Remove that statement from the task roles after the AWS go-live, or keep it only for debugging (gate at G7).

**Not included yet:** the CI/CD deploy role `mentor-github-deploy` (GitHub OIDC trust limited to your repo's `main` branch and the approval environment; permissions to run Terraform on `project=mentor` resources and read/write the state bucket), added by the Terraform `github_oidc` module in Phase 6; and an extra Bedrock action, needed only if G1 picks Bedrock's OpenAI-compatible endpoint instead of the Converse API.

## What happens when you approve this plan

1. I save this plan into the project as `/Users/sachin/sachil ai agent/IMPLEMENTATION_PLAN.md`. That is the only action.
2. **No implementation starts.** You continue refining the plan. When you say "start Phase 0", I ask gate **G0** first, and every later gate (G1–G12) before the step it covers.

## Sources checked (15 Oct 2026)
- AWS: OpenAI GPT-5.6 India cross-region inference on Bedrock — aws-news.com 2026-08-27; aws.amazon.com/blogs/machine-learning/introducing-openai-models-on-amazon-bedrock-for-in-country-inferencing-in-india/
- AWS: Claude in India via Bedrock global cross-region inference — aws.amazon.com/blogs/machine-learning/access-anthropic-claude-models-in-india-on-amazon-bedrock-with-global-cross-region-inference
- gpt-oss pricing on Bedrock (third-party trackers; must check against official AWS pricing at G1) — futureagi.com, typingmind.com
- Apple Foundation Models languages/tool calling — natashatherobot.com/p/apple-foundation-models

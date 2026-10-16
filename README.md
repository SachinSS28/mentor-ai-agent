# Mentor

A personal AI chief of staff and coach: daily brief, routines, tasks, meetings, travel and research, acting only within strict approval rules.

- Product blueprint: [MENTOR_AI_Agent_Plan.md](MENTOR_AI_Agent_Plan.md)
- Build plan, decisions and status: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- AWS permissions and setup: [aws-iam/](aws-iam/)

## Development

```sh
uv sync                     # Python 3.13 + dev tools
uv run pre-commit install   # lint, type-check and secret checks on every commit
uv run pytest
```

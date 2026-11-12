# CLAUDE.md — SATurn

AI-assisted school timetable maker for the National Autumn IT Tournament "Джон Атанасов"
(4–6.12.2026, Sofia, category XI–XII "Разпределени приложения").
Full technical plan: [TECH_STACK.md](TECH_STACK.md). Read the relevant section before changing architecture.

## Core principle

**The LLM reads text and proposes constraints. It never places lessons.**
Admin text → LLM proposes constraints (strict JSON) → admin confirms or rejects → CP-SAT solver builds the
timetable from **confirmed** constraints only → an independent checker verifies it.
The app must work end to end with `LLM_ENABLED=false`.

## Hard rules

1. Never let an LLM place lessons. LLM output is only a *proposed* constraint and needs admin confirmation.
2. `solver/`, `checker/` and `schema/` never import Flask, SQLAlchemy or anything from `app/`. Plain data in, plain data out.
3. `checker/` never imports from `solver/`. It re-implements every rule on its own.
4. Tests go in the same change as the code. A solver change without a checker test is not done.
5. No secrets, real names or real school data in the repo. Use the anonymized data in `seeds/`.
6. No new constraint type, table or dependency without stating why. Prefer what is already in the stack.
7. **Ask before changing the constraint JSON format or the database schema.** Both developers depend on them.
8. Never invent Bulgarian school regulations. If a rule is unspecified, state the assumption in a code
   comment and in your reply, and list it as an open question (see TECH_STACK.md §15).
9. **Feature freeze: 26.11.2026.** After that date: fix, polish and test only. No new features.
10. User-facing text is Bulgarian (correct literary Bulgarian). Code, comments and commit messages are English.

## Team and tasks

| Area | Owner | Scope |
|---|---|---|
| Backend `BE-xx` | Boyan (@protoknight12) | repo/CI, deploy, DB models, auth, constraint models, solver, background jobs, LLM |
| Frontend `FE-xx` | Hristian | base layout, routes + templates for pages, checker, demo data, Excel export, slides |
| Shared `TEAM-xx` | both | rules, decisions, data contract, testing, docs, registration |

- Tasks are GitHub issues titled `BE-05 …`, `FE-04 …`, `TEAM-03 …`, tracked on the Project board
  "SATurn backlog" (views: All tasks, Backend, Frontend). Numbers follow build order.
- Each issue lists owner, due date, estimate, dependencies and a "Done when" checklist. Work to that checklist.
- Stay inside the task you were given. If it needs work from another owner's area, say so instead of doing it.
- The checker is deliberately written by a different person than the solver. Keep it that way.

## Workflow

- Branch per task: `be-05-login`, `fe-04-checker`, `team-03-schema`.
- Small, focused commits. Don't rewrite unrelated files.
- Every change goes through a PR into `main`, reviewed by the other developer. Put `Closes #<issue>` in the PR.
- Before opening a PR: run lint and tests (commands below once the skeleton exists) and make sure CI is green.

## Commands

```bash
docker compose up --build          # web on http://localhost:8000, plus worker, db, redis
uv sync                            # local dev environment (Python 3.12)
uv run pytest                      # tests
uv run ruff check . && uv run ruff format --check .   # lint + format check (CI runs both)
uv add <pkg>                       # add a dependency; commit pyproject.toml and uv.lock together
```

Migrations and seeding: added in BE-04.

## Stack (short)

Python 3.12 · Flask + Jinja2 + HTMX (server-rendered, no SPA) · SQLAlchemy 2.x (2.0-style `select()`) ·
PostgreSQL 16 (constraints as JSONB) · Alembic · Redis + RQ · OR-Tools CP-SAT · Pydantic v2 (single source
of truth for the constraint format; the LLM's JSON Schema is generated from it, never hand-written) ·
Flask-Login · openpyxl · uv · ruff · pytest · GitHub Actions · Docker Compose (`web`, `worker`, `db`, `redis`).

## Tournament requirements that affect code

- The app must be **online** during evaluation and defense, with **test accounts for every role**
  (Администратор, Преглед). Never break the deployed demo; no deploys 01.12–06.12 except critical fixes.
- The jury scores security (CSRF, rate limits, error handling), automated tests, version-control use,
  UI readability in light and dark mode, and correct Bulgarian.
- The public LLM endpoint must have an input length cap, a per-user rate limit and a spend limit.

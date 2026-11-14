# SATurn

AI-assisted school timetable maker, built for the National Autumn IT Tournament "Джон Атанасов" (December 2026, Sofia).

> **Status:** in development. Merged so far: data contract, constraint models, DB models, login with roles, solver core.
> Not yet: background jobs, LLM, data entry pages, deploy. Feature freeze: **26.11.2026**.

## Core principle

**The LLM reads text and proposes constraints. It never places lessons.**

1. An admin describes requirements in plain Bulgarian text, or fills in a form.
2. The LLM turns the text into *proposed* constraints in a strict JSON format.
3. An admin confirms or rejects each proposed constraint.
4. The solver (OR-Tools CP-SAT) builds the timetable from confirmed constraints only.
5. A deterministic checker independently verifies the result and reports violations.

The app must work end to end with the LLM switched off.

## Stack

Python 3.12, Flask, SQLAlchemy 2.x, PostgreSQL 16, Redis + RQ, OR-Tools CP-SAT, Pydantic v2, Flask-Login,
Flask-WTF, Flask-Limiter, Bootstrap 5.3, Docker Compose.
Full details and open decisions: [TECH_STACK.md](TECH_STACK.md).

## Repository layout

| Folder | Purpose |
|---|---|
| `app/` | Flask app. The only place that touches the database |
| `schema/` | Pydantic constraint models (no Flask/DB imports) |
| `solver/` | CP-SAT model (no Flask/DB imports) |
| `checker/` | Independent verifier: `check(solver_input, result)` returns violations (no Flask/DB/solver imports) |
| `seeds/` | Anonymized demo data only |
| `migrations/` | Alembic migrations |
| `tools/` | Seed conversion and the solver benchmark (`tools/bench_solver.py`, not run in CI) |
| `tests/` | Tests for checker, solver and app |

## Getting started

```bash
cp .env.example .env
docker compose up --build    # http://localhost:8000
```

For local development without Docker: `uv sync`, then `uv run pytest`.

Database (PostgreSQL via `DATABASE_URL`; without it the app uses a throwaway in-memory SQLite):

```bash
uv run flask --app app db upgrade   # create/update the schema
uv run flask --app app seed         # load the demo school into an empty DB (--reset replaces it)
uv run flask --app app create-user NAME --role admin|viewer [--password-env VAR]
```

`flask seed` also creates the demo accounts `admin` (Администратор) and `preglad` (Преглед) when
`DEMO_ADMIN_PASSWORD` / `DEMO_VIEWER_PASSWORD` are set in `.env`; otherwise it skips them.

DB tests run on SQLite by default; set `TEST_DATABASE_URL` to run them on PostgreSQL (CI does).
Configuration variables are documented in [.env.example](.env.example).

## Viewing the frontend

The frontend is server-rendered Flask + Jinja2 with Bootstrap 5.3 (no build step, no SPA).

**Fastest way (no Docker, no database needed yet):**

```bash
uv sync
uv run flask --app app run --debug    # http://127.0.0.1:5000
```

`--debug` reloads the server and the templates when you save a file. Then open:

| URL | Page |
|---|---|
| `/` | Home: welcome text and a "Вход" button |
| `/login` | Login form (username, password, "Запомни ме") |
| `/logout` | POST only, from the navbar |
| `/data/teachers/`, `/data/subjects/`, `/data/rooms/`, `/data/classes/`, `/data/periods/` | Data lists (navbar "Данни"); admins also get add/edit/delete. Extra groups of a class are on its edit page |

**With Docker:** `docker compose up --build`, then open http://localhost:8000 (gunicorn, no auto-reload).

Things to try:

- **Dark mode:** click the "Тъмна тема" button in the navbar. The choice is saved in the browser (`localStorage`);
  on the first visit it follows the operating system setting.
- **Mobile layout:** narrow the window below 992 px. The navbar collapses into a hamburger menu.

Current limits:

- Login works (BE-05): roles `admin` (Администратор, may change data) and `viewer` (Преглед, read only),
  CSRF on every form, and login attempts limited to 5 per minute. Create an account first (see above).
- The "Разписание" menu item is disabled until its page exists.
- Bootstrap and Bootstrap Icons load from the jsDelivr CDN, so the pages look unstyled without an internet connection.

### Frontend layout

| Path | Purpose |
|---|---|
| `app/templates/base.html` | Base layout: navbar, theme toggle, flashed messages, `{% block title %}` and `{% block content %}` |
| `app/templates/index.html`, `login.html` | Pages; each starts with `{% extends "base.html" %}` |
| `app/static/css/app.css` | Our own CSS on top of Bootstrap (keep it small) |
| `app/static/js/theme.js` | Light/dark toggle (an inline script in `base.html` sets the theme before first paint to avoid a flash) |
| `app/routes/pages.py` | Routes that render the pages (`pages` blueprint) |

New pages: add a template that extends `base.html`, add a route in `app/routes/`, and add a test in `tests/app/`.
Keep all user-facing text in Bulgarian and make sure every page is readable in both light and dark mode.

## Ground rules

- `solver/` and `checker/` never import Flask, SQLAlchemy or anything from `app/`.
- Every code change comes with tests. A solver change without a checker test is incomplete.
- No secrets, real names or real school data in the repository.
- User-facing text is Bulgarian. Code, comments and commit messages are English.
- Changes to the constraint JSON format or the database schema are agreed by both developers first.

## License

Not decided yet.

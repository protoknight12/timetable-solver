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
- Every change goes through a PR into `main`. No reviewer is required; the CI `test` check must pass
  before merging (branch protection). Put `Closes #<issue>` in the PR.
- Before opening a PR: run lint and tests (commands below).

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

## Frontend conventions

- Server-rendered Jinja2 pages that extend `app/templates/base.html`; Bootstrap 5.3 from CDN (no build step); HTMX only where a page needs it.
- Theme is controlled by `data-bs-theme` on `<html>` (set in `base.html`, toggled by `static/js/theme.js`). Use Bootstrap
  theme-aware classes (`bg-body-tertiary`, `text-body`, ...) and never hard-code colors, so dark mode stays readable.
- Routes live in `app/routes/` (blueprint `pages` for plain pages). Link with `url_for`, never hard-coded paths.
- Forms that change data must include the CSRF token (`login.html` shows the pattern; real CSRF arrives with BE-05).
- How to run and view the pages locally: README.md, section "Viewing the frontend".

## Visual style (keep every new page consistent)

This documents what the existing pages already do (`base.html`, `index.html`, `login.html`). Match it; don't introduce a second look.

- **Look:** clean, calm, utilitarian Bootstrap 5.3 defaults. Standard system font stack, no custom fonts, no custom palette, no gradients or heavy shadows.
- **Color:** only Bootstrap theme variables. Brand/primary color is the default Bootstrap primary (blue): `btn-primary` for the one main action per page, `btn-outline-secondary` for secondary/utility actions, `bg-body-tertiary` for the navbar. Never hard-code hex colors or `bg-white`/`text-dark`/`bg-light`; they break dark mode. If a custom color is unavoidable, define it in `app.css` using `var(--bs-*)`.
- **Light and dark:** every page must be readable in both. Check both before opening a PR. Default follows the OS, the toggle persists the choice.
- **Layout:** content sits in `<main class="container py-4">`; the page below the navbar scrolls, the navbar does not change. Mobile first; the navbar collapses below `lg`. Use the Bootstrap grid (`row`/`col-*`), not fixed widths.
- **Headings:** one `h1` per page. Page titles use Bootstrap heading classes (`h2` for pages like the home page, `h4` inside cards). Intro text uses `lead`.
- **Cards and forms:** forms live in a `card shadow-sm` with `card-body p-4`. Inputs use `form-floating` with a Bulgarian label, `mb-3` spacing, correct `autocomplete`, `maxlength` and `required`. Submit button is `btn btn-primary w-100` inside narrow cards.
- **Icons:** Bootstrap Icons only (`bi bi-*`), always next to a text label, never alone (exception: the navbar brand icon).
- **Feedback:** flashed messages render as dismissible Bootstrap alerts in `base.html` (`success`, `danger`, `warning`, `info`). Don't build your own banners.
- **Navigation:** the navbar entry for the current page gets `active` and `aria-current="page"`. Items for pages that don't exist yet are `disabled`.
- **Accessibility:** every control has a label or `aria-label` (Bulgarian), keep visible focus outlines, and don't convey meaning by color alone.
- **Text:** all visible text is correct literary Bulgarian, sentence case, short and direct ("Вход", "Запомни ме"). Buttons are verbs or short nouns, no exclamation marks except the welcome heading.
- **CSS:** put custom rules in `app/static/css/app.css`, keep it small, prefer Bootstrap utilities. No inline `style=""`, no extra CSS framework.

## Tournament requirements that affect code

- The app must be **online** during evaluation and defense, with **test accounts for every role**
  (Администратор, Преглед). Never break the deployed demo; no deploys 01.12–06.12 except critical fixes.
- The jury scores security (CSRF, rate limits, error handling), automated tests, version-control use,
  UI readability in light and dark mode, and correct Bulgarian.
- The public LLM endpoint must have an input length cap, a per-user rate limit and a spend limit.

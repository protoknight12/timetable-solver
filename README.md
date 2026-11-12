# SATurn

AI-assisted school timetable maker, built for the National Autumn IT Tournament "Джон Атанасов" (December 2026, Sofia).

> **Status:** planning. Feature freeze: **26.11.2026**.

## Core principle

**The LLM reads text and proposes constraints. It never places lessons.**

1. An admin describes requirements in plain Bulgarian text, or fills in a form.
2. The LLM turns the text into *proposed* constraints in a strict JSON format.
3. An admin confirms or rejects each proposed constraint.
4. The solver (OR-Tools CP-SAT) builds the timetable from confirmed constraints only.
5. A deterministic checker independently verifies the result and reports violations.

The app must work end to end with the LLM switched off.

## Planned stack

Python 3.12, Flask, SQLAlchemy 2.x, PostgreSQL 16, Redis + RQ, OR-Tools CP-SAT, Pydantic v2, Docker Compose.
Full details and open decisions: [TECH_STACK.md](TECH_STACK.md).

## Repository layout

| Folder | Purpose |
|---|---|
| `app/` | Flask app. The only place that touches the database |
| `schema/` | Pydantic constraint models (no Flask/DB imports) |
| `solver/` | CP-SAT model (no Flask/DB imports) |
| `checker/` | Independent verifier (no Flask/DB imports) |
| `seeds/` | Anonymized demo data only |
| `tests/` | Tests for checker, solver and app |

## Getting started

Not available yet. Setup instructions will be added once the first runnable skeleton exists.
Configuration variables are documented in [.env.example](.env.example).

## Ground rules

- `solver/` and `checker/` never import Flask, SQLAlchemy or anything from `app/`.
- Every code change comes with tests. A solver change without a checker test is incomplete.
- No secrets, real names or real school data in the repository.
- User-facing text is Bulgarian. Code, comments and commit messages are English.
- Changes to the constraint JSON format or the database schema are agreed by both developers first.

## License

Not decided yet.

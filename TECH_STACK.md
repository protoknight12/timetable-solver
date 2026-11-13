# SATurn — Technical Overview

> **Name:** SATurn (decided 12.11.2026).
> **Project:** AI-assisted school timetable maker for the National Autumn IT Tournament "Джон Атанасов" (December 2026, Sofia).
> **Team:** two students + a teacher supervisor.
> **Status:** in development (data contract, constraint models, DB models, auth and solver core merged on 14.11.2026). **Feature freeze: 26.11.2026.**

Items marked **[DECIDE]** still need agreement between both developers.
Items marked **[QUESTION]** are unknowns about real school rules. Do not guess them in code.

---

## 1. Core principle

**The LLM reads text and proposes constraints. It never places lessons.**

1. An admin describes requirements in plain Bulgarian text (or fills in a form).
2. The LLM turns the text into **proposed** constraints in a strict JSON format.
3. An admin **confirms or rejects** each proposed constraint.
4. The **solver (OR-Tools CP-SAT)** builds the timetable using only confirmed constraints.
5. A **deterministic checker** independently verifies the result and reports any violations.

The app must work end to end **with the LLM switched off**. Manual constraint entry is a first-class path, not a fallback.

---

## 2. Architecture

```
            ┌──────────────┐
 Browser ──►│  web (Flask) │──── Postgres (data, constraints, results)
 (Jinja +   │  + Gunicorn  │
  HTMX)     └──────┬───────┘
                   │ enqueue solve job
                   ▼
              ┌─────────┐        ┌──────────────────────────┐
              │  Redis  │◄──────►│ worker (RQ)              │
              └─────────┘        │  loads data → plain dicts│
                                 │  solver/  (CP-SAT)       │
                                 │  checker/ (pure Python)  │
                                 │  saves result + report   │
                                 └──────────────────────────┘

 web ──► Anthropic API (only for turning text into proposed constraints)
```

**Boundary rule:** `solver/` and `checker/` never import Flask, SQLAlchemy or anything from `app/`. The worker converts DB rows into plain data, calls them, and saves the plain output.

---

## 3. Tech stack

### 3.1 Core

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Both devs on the same minor version |
| Web framework | Flask + Jinja2 | Server-rendered, no SPA |
| WSGI server | Gunicorn | Inside the `web` container |
| ORM | SQLAlchemy 2.x | 2.0-style `select()` queries |
| Database | PostgreSQL 16 | Constraints stored as `JSONB` |
| Migrations | Alembic (via Flask-Migrate) | Every schema change is a committed migration file |
| Solver | OR-Tools CP-SAT | Time limit + `num_workers`; always keep the best solution found |
| Checker | Plain Python, no dependencies | Must not reuse solver code |
| Background jobs | Redis + **RQ** | Chosen over Celery: far less config, and the extra Celery features aren't needed |
| Deployment | Docker Compose | Services: `web`, `worker`, `db`, `redis` |

### 3.2 Additions (with reasons)

| Need | Choice | Why |
|---|---|---|
| Constraint format | Pydantic v2 | One source of truth: validates LLM output, generates the JSON Schema given to the LLM, documents the format |
| LLM access | Anthropic Python SDK, tool use / structured output | Forces LLM output into the Pydantic schema; invalid output is dropped before an admin sees it |
| Interactivity | HTMX + a little Alpine.js | Job status polling, confirm/reject buttons, small grid edits without a JS build |
| Styling | Bootstrap 5.3 + Bootstrap Icons via CDN (decided, used since FE-02) | No build step; built-in dark mode via `data-bs-theme` |
| Auth | Flask-Login | Already familiar from earlier projects |
| Forms / security | Flask-WTF (CSRF), Flask-Limiter (rate limits, Redis storage) | The jury scores CSRF and rate limiting (BE-05) |
| Excel export | openpyxl | Schools will want the timetable as a spreadsheet |
| ~~PDF export~~ | ~~WeasyPrint~~ | Cut until after the tournament (#35) |
| Packaging | uv + `pyproject.toml` | Fast, reproducible installs |
| Lint / format | ruff | One tool for both |
| Tests | pytest | |
| CI | GitHub Actions | ruff + pytest on every push |

---

## 4. Repository layout (proposed)

```
.
├── app/                    # Flask app — the only place that touches the DB
│   ├── __init__.py         # app factory
│   ├── models.py
│   ├── routes/
│   ├── templates/          # all user-facing text in Bulgarian
│   ├── llm/                # prompt + Anthropic client + parsing
│   └── jobs.py             # RQ job: DB → plain data → solver → checker → DB
├── schema/                 # Pydantic constraint models (no Flask/DB imports)
├── solver/                 # CP-SAT model (no Flask/DB imports)
├── checker/                # independent verifier (no Flask/DB imports)
├── seeds/                  # anonymized demo data only
├── migrations/             # Alembic
├── tests/
│   ├── checker/
│   ├── solver/
│   └── app/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example            # committed; .env is not
├── LICENSE
└── README.md
```

---

## 5. Data model (agreed 13.11.2026, TEAM-03)

The solver/checker contract is code: [`schema/contract.py`](schema/contract.py), with a full example in
[`schema/examples/split_practice.json`](schema/examples/split_practice.json). The DB models (BE-04) mirror it.

**Core idea.** A `Course` is one scheduled unit (periods per week split into blocks); the solver gives each
block a day and a start period. A `CoursePart` is what one set of student groups does during that unit. All
parts of a Course happen at the same time, which covers joint lessons (one part, groups from several classes),
parallel split lessons ("ОС практика OR БД практика": two parts) and "one half has nothing" (one part; the
other group just has no part).

| Entity | Key fields |
|---|---|
| `Teacher` | id, display name (anonymized in seeds) |
| `Subject` | id, name, default room type (nullable) |
| `SchoolClass` | id, name (e.g. "10А"), grade |
| `StudentGroup` | id, class, name, `is_whole`, `split`. Every class has exactly one whole-class group ("цял клас"); splits add more ("Група 1"). `split` names the way the class is divided ("групи", "езици"): groups of the same split never share students, groups of different splits may (a Chinese student is also in Група 1 or 2) |
| `Room` | id, name, type, `concurrent_capacity` (default 1; sports hall 3) |
| `Period` | number, start time, end time. Days are 1–5 |
| `Course` | id, periods per week, `block_sizes` (e.g. `[1,1,1]`, `[2,1]`, `[2]`), optional label |
| `CoursePart` | id, course, subject, `needs_room`, room type, allowed rooms |
| `CoursePartGroup` / `CoursePartTeacher` | many-to-many; all listed teachers are present at once |
| `Constraint` | id, `type`, `params` (JSONB), `hard`/`soft`, `weight`, `status` (proposed / confirmed / rejected), `source` (manual / llm), original text |
| `SolveJob` | id, status, started/finished, solver status, objective value, error, `base_job_id` (nullable, for minimal-change re-solve #39) |
| `Lesson` | job, course, part, day, start period, length, room. One row per part per block |
| `Violation` | job, constraint type, involved entities, message key |

`TeacherSubject` (can teach / can substitute) is dropped until substitutions exist (after the tournament).

**Broken references.** Constraint params hold ids inside JSONB, so the database cannot enforce them.
`SolverInput.problems()` reports constraints that point at a missing teacher, subject, course, class or room
type, and the worker must refuse to solve and show those problems instead of silently ignoring them.

**Limits.** Every lesson repeats weekly (no alternating weeks). Parts of one Course share the same block
pattern. Two blocks of one Course may share a day.
**Assumption, not confirmed:** the solver currently requires at least one free period between them, but the
real seed has one counterexample. See §15 q10.

---

## 6. Constraint format (agreed 13.11.2026, TEAM-03; implemented in `schema/constraints.py`, BE-03)

- Each constraint is a Pydantic model with a fixed `type` literal and typed `params`.
- The JSON Schema for the LLM is **generated** from these models, never hand-written.
- Every constraint is either **hard** (must hold) or **soft** (penalty × weight in the objective).
- Changing this format affects both developers: discuss before merging.

Example:

```json
{
  "type": "teacher_available_days",
  "hard": true,
  "params": { "teacher_id": 12, "days": [5] },
  "source": "llm",
  "original_text": "Иванова може да преподава само в петък."
}
```

Generic constraint types (not tied to any regulation). The first five plus `fixed_slot` are implemented in the solver (BE-07); the other two are Could (TEAM-11):

| Type | Meaning |
|---|---|
| `teacher_available_days` | Teacher works only on the listed days |
| `teacher_unavailable_slots` | Teacher cannot teach in the listed slots |
| `room_type_required` | Subject must be in a room of a given type |
| `max_lessons_per_day` | Class has at most N lessons per day |
| `max_same_subject_per_day` | Class has a subject at most N times per day |
| `no_gaps` (soft or hard) | No empty periods between a class's lessons |
| `teacher_max_lessons_per_day` | Teacher teaches at most N lessons per day |

Built-in hard rules (always on, not stored as constraints): a teacher is in at most one part-block per slot; two groups of the same class conflict if either is the whole class, they are the same group, or they belong to different splits, and conflicting groups never share a slot; a room holds at most `concurrent_capacity` part-blocks per slot; every `Course` gets exactly its `block_sizes`, each block being consecutive periods on one day; a part that needs a room gets one matching its room type and allowed rooms.

Extra type added with the data model: `fixed_slot` pins every block of a Course, e.g. a practice that must be on Friday periods 1–2. Params: `course_id`, `blocks: [{day, start_period}]`, one entry per block in `block_sizes` order.

---

## 7. LLM integration

- **Input:** free text from an admin, plus the current lists of teachers, subjects, classes and rooms (IDs + names) so the LLM can reference them.
- **Output:** a list of constraint objects via tool use, validated with Pydantic.
- **Unknown names or unparseable sentences** come back as "not understood" items shown to the admin, never guessed.
- **Everything the LLM produces is saved as `status = proposed`.** The solver only reads `confirmed`.
- **Config:** model name, API key and an `LLM_ENABLED` flag come from env vars. With `LLM_ENABLED=false` the text input is hidden and only the manual form is shown.
- Log the input text and raw LLM output for each request (useful for debugging and for showing the jury).

---

## 8. Solver

- Decision variables: one start variable per Course block (day + start period), plus room assignment per part where needed. Implemented in `solver/` (BE-06, BE-07).
- Hard constraints as CP-SAT constraints; soft constraints as weighted penalty terms in a minimized objective.
- Default time limit: 60 s (`SOLVER_TIME_LIMIT_S`); configurable per job.
- Return: status (`OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `UNKNOWN`), lessons, objective value, solve time.
- **Infeasible results must be explained.** Attach an assumption literal to each user constraint and use CP-SAT's `sufficient_assumptions_for_infeasibility()` to report which constraints conflict.
- Input and output are plain dataclasses or dicts. No DB access.

---

## 9. Checker

> **Status:** not started (`checker/` is empty). Owner: Hristian (FE-04). Until it exists, the solver tests use their own verification, written by the solver's author, so they are not independent.

- Input: the same plain data the solver got + the solver's lessons.
- Re-checks every built-in rule and every confirmed constraint, using its own code.
- Output: a list of violations (`type`, involved entity IDs, `message_key`). The web layer turns `message_key` into Bulgarian text.
- Runs automatically after every solve. The UI shows the checker report next to the timetable.
- It also checks the final timetable after any manual edits.

---

## 10. Testing

| Area | What is tested |
|---|---|
| Checker | One test per constraint type: a valid timetable passes, a hand-built violating one fails with the right violation |
| Solver | Solve each seed dataset → run checker → expect zero hard violations |
| Solver | A deliberately impossible dataset → `INFEASIBLE` with the conflicting constraints named |
| Schema | Valid/invalid constraint JSON accepted/rejected |
| LLM | Parsing tested against **recorded** responses; no live API calls in CI |
| App | Smoke tests for main routes against a test Postgres |

Rule: a solver change without a checker test is not done.

---

## 11. Configuration & secrets

Env vars (documented in `.env.example`):

```
SECRET_KEY=
DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://redis:6379/0
ANTHROPIC_API_KEY=
LLM_MODEL=
LLM_ENABLED=true
SOLVER_TIME_LIMIT_S=60
```

- Never commit `.env`, API keys, real names or real school data.
- Seeds use made-up teachers and classes only.

---

## 12. Conventions

- **User-facing text:** Bulgarian.
- **Code, comments, commit messages, this doc:** English.
- Small, focused commits; no drive-by rewrites of unrelated files.
- Any assumption gets a code comment **and** a mention in the PR/commit description.
- New constraint types, tables or dependencies need a stated reason.

---

## 13. Scope & timeline

| Date | Milestone |
|---|---|
| 12.11 | Stack, name, license decided; repo + Docker Compose skeleton |
| ~15.11 | Schema + constraint format agreed; seeds; manual data entry |
| ~19.11 | Solver + checker working on seeds, with tests |
| ~22.11 | LLM constraint proposals + confirm/reject UI; timetable grid view |
| ~24.11 | Excel export; infeasibility explanations |
| **26.11** | **Feature freeze** — fix, polish, test only |
| December | Tournament |

**MVP (must ship):** data entry → constraints (form + LLM) → solve → checker report → grid view → one export.

**After the tournament:** substitutions, drag-and-drop editing, PDF export (cut, #35), multiple schools, teacher-facing views.

---

## 14. License

**Proposed: MIT** **[DECIDE]** — agree with both team members.

- Short and permissive; anyone may use, modify and deploy the code if they keep the copyright notice.
- All core dependencies are permissively licensed, so no dependency forces a different choice. Redis has a more restrictive license but runs as a separate service and does not affect this code.
- Alternative if the team wants modified hosted versions to stay open: **AGPL-3.0**.
- Copyright line should name **both** developers.
- Check the tournament regulations for any IP or licensing requirements before publishing.

---

## 15. Open questions

**About school rules [QUESTION] — ask the supervisor, don't guess:**

1. How many periods per day, and does it differ by grade?
2. Does the school work in one shift or two, and do classes move between shifts?
3. Are gaps in a class's day allowed, discouraged or forbidden?
4. Are double periods (two lessons of the same subject back to back) required or allowed for any subject?
5. Are there limits on lessons per day per grade, or on how heavy subjects are spread across the week?
6. How do group splits work (e.g. foreign languages, IT split into two groups)?
7. What should a teacher's maximum load per day be?
8. Practice slots where both groups of a class appear in two lessons at once: weekly rotation, or truly parallel? (Parallel fits the model; rotation does not.)
9. PE slots where one teacher has overlapping blocks for different classes: allowed, or an aSc export artifact?
10. May one subject have two blocks on the same day? The real seed's 8th-grade English needs it, so the solver
    allows it. **Open:** must there be a free period between them? The solver currently enforces one
    (e.g. English 3, Math 2, English 2), but the real timetable has one case with the blocks back to back.
11. When one group has no lesson (the "or nothing" case), are its free periods at the start or end of the day acceptable, and do they count as gaps? Current assumption: not gaps.
12. Is it acceptable that `seeds/example_school.json` keeps the real school's subjects, rooms and bell times (teacher and class names are generated)? Decide with the team and the supervisor; options: keep with permission, anonymize further, or keep it local only.

**Team decisions [DECIDE]:**

1. License.
2. ~~Data model and constraint format (sections 5–6).~~ Agreed 13.11.2026 (TEAM-03).
   Contract v2 (`StudentGroup.split`, PR #49) and the same-day block rule (PR #48) were added on 14.11 and still need Hristian's explicit OK.
3. ~~Tailwind or Bootstrap.~~ Bootstrap 5.3.
4. ~~Default solver time limit.~~ 60 s.
5. ~~Laptop or hosted demo?~~ Hosted VPS with a public HTTPS URL (required by the rules), plus an offline laptop copy and a recorded video as backup.

# SATurn: defense notes

For Hristian, for building the slides (22.11.2026). Everything here is checked against the code on `main` and PR #112.
Category: XI–XII "Разпределени приложения". Tournament: 4–6.12.2026, Sofia.

> **Check first:** the 2026 regulation (TEAM-01) is still unread, so the defense length, slide limit and required
> structure are not known yet. Don't fix the number of slides until Boyan confirms them.

---

## 1. The pitch in one breath

Making a school timetable by hand, or in aSc, takes the deputy principal days, and every change means starting over.
**SATurn** lets the admin type the school's rules in plain Bulgarian. An AI turns the text into *proposed* rules, and the
admin confirms them. A mathematical solver then builds a timetable that is **guaranteed** to follow every confirmed rule,
and an independent checker proves it.

Suggested slide line (BG): *„ИИ разбира правилата. Математиката подрежда часовете. Проверката го доказва.“*

---

## 2. The one decision to defend above all

**The LLM never places lessons. It only proposes rules, and a person confirms them.**

| Option | Why we did not / did choose it |
|---|---|
| Let the LLM write the timetable | It invents clashes, can't guarantee a teacher isn't in two rooms at once, gives a different answer every time, and can't explain an impossible request. A timetable with one clash is useless. |
| Only forms, no AI | Works, and it's always available as the fallback (`LLM_ENABLED=false`), but describing 50 teachers' wishes in forms is slow. |
| **AI → proposed rule → admin confirms → solver** (chosen) | The AI does what it's good at (understanding Bulgarian text). The solver does what it's good at (exact search with guarantees). Nothing the AI says reaches the timetable without a human clicking "Потвърди". |

Points to say out loud:
- The app works **end to end with the AI switched off**. The AI is a convenience, not a dependency.
- Everything the AI produces is shown with an **„ИИ“** badge and the original sentence, so the admin knows where each rule came from.
- If the AI doesn't understand a name or a sentence, it says so. It doesn't guess.

---

## 3. Architecture (the "distributed" part)

Four services in Docker Compose:

```
Browser ──► web (Flask + Gunicorn) ──► PostgreSQL 16 (data, rules as JSONB, timetables)
                 │
                 │ enqueue "solve" job
                 ▼
               Redis ◄──► worker (RQ): DB → plain data → CP-SAT solver → checker → DB

web ──► Anthropic API (only text → proposed rules)
```

Why it's split like this (good for the "Разпределени приложения" category):
- **Solving runs in a separate worker process**, not in the web request. A big school can take up to 60 s; the page stays responsive and polls the job status with HTMX.
- **The solver and the checker are pure Python packages** with no Flask or database imports. Plain data in, plain data out. They can be tested alone, moved to another machine, or reused by another app.
- **One `install.sh`** sets up the whole stack on any Linux server with Docker: random secrets, migrations, first admin account. Product story: each school hosts its own copy in its own network.

---

## 4. Technology choices and why

| Need | Choice | Why (and what we rejected) |
|---|---|---|
| Timetable search | **Google OR-Tools CP-SAT** | Industrial constraint solver, free, Python API. Proves "optimal" or "impossible". Rejected: genetic algorithms / hand-written heuristics (no guarantees, no explanation when impossible). |
| Independent check | **Own checker in plain Python** | Re-implements every rule from the written spec without importing or copying solver code, so a solver bug can't hide in the check too. |
| Web | **Flask + Jinja2 + HTMX + Bootstrap 5.3** | Server-rendered, no JavaScript build step, two developers can both work on it. Bootstrap gives dark mode for free. Rejected: React SPA (twice the code for the same result). |
| Database | **PostgreSQL 16**, rules stored as **JSONB** | New rule types don't need a new table each time. |
| Rule format | **Pydantic v2** | One definition of every rule type. The JSON Schema given to the AI is *generated* from it, so the AI can't produce a rule shape the solver doesn't know. |
| Background jobs | **Redis + RQ** | Much simpler than Celery; we don't need Celery's extra features. |
| AI | **Anthropic Claude (Sonnet 5.5)** with tool use | Good Bulgarian, structured output. Each school uses its own API key. |
| Deploy | **Docker Compose + Caddy** (automatic HTTPS) | Same setup on the VPS and at a school. |
| Quality | **pytest, ruff, GitHub Actions** | Every PR runs lint + all tests on PostgreSQL before it can be merged. |
| License | **MIT** | Short, permissive, all dependencies compatible. |

Why not train our own AI model: no dataset, no GPUs at schools, weaker Bulgarian in small open models, and retraining for every new rule type. The confirmed/rejected proposals are logged, so they could become a training set later.

---

## 5. What the app does (feature list for a slide)

**Data in**
- Forms for teachers, subjects, rooms, classes (with groups/splits), periods, curriculum („Учебен план“).
- Import from an Excel/CSV template, and **import of a school's aSc export** (the program most Bulgarian schools use).

**Rules (14 types, plus built-in rules)**
- Teacher: available days, unavailable slots, max lessons per day, min lessons per day, max gaps („прозорци“).
- Class: max lessons per day, no gaps, day starts with first period, unavailable slots, max times one subject per day.
- Subject/room: room type required, subject unavailable slots, periods reserved for subjects (e.g. PE in periods 9–11), fixed slot.
- Every rule is **hard** (must hold) or a **preference** with a weight.
- Built in, always on: a teacher, group or room is never double-booked; room seats ≥ students; computer rooms for subjects that need computers.
- Split classes (language groups, practice groups), joint lessons of several classes, alternating **A/B weeks**.

**Solve and check**
- Background solve; result is `OPTIMAL` / `FEASIBLE` / `INFEASIBLE`.
- **When it's impossible, it says why:** it names the exact rules that clash, and offers a button to turn them into preferences and solve again.
- Checker report next to every timetable.
- **Minimal-change re-solve:** add a rule after the timetable is published, and the new timetable moves as few lessons as possible.
- Manual editing: move a lesson, check it, pin it.

**Out**
- Grid per class, per teacher, whole-school overview.
- Export to **Excel** and **PDF** (A3 overview).
- Versions: publish, history, diff between versions.
- Public read-only class page with a **QR code** (for parents/students), rate-limited.
- Teacher workload report (hours, days, gaps) with the teacher's weekly norm („хорариум“) and over/under warnings (PR #112).

**Users**
- Roles **Администратор** (changes data) and **Преглед** (read only). Teachers can be linked to a viewer login and see only their own workload.

---

## 6. Numbers to show

| Fact | Value |
|---|---|
| Example school (anonymized real aSc export) | 27 classes, 56 teachers, 64 subjects, 40 rooms, 648 lessons |
| Solve time, example school | about 3 s to OPTIMAL (re-solve ≈ 3.6 s) |
| Solve time, demo school | about 0.6 s |
| Automated tests | 743, all passing (22.11) |
| Git history | 174 commits, PRs #1–#112, every change through a PR with CI |
| Rule types | 14 + built-in rules |

Re-measure the solve times on the final build before putting them on a slide.

---

## 7. Security (the jury scores this explicitly)

- **CSRF token** on every form that changes data (Flask-WTF).
- **Rate limits:** login 5/min, solve 5/min, AI requests 5/min per admin and 100/day for the whole app, public page 60/min per IP.
- **AI cost control:** input capped at 1000 characters, per-user and daily limits, plus a hard monthly spend limit on the API key.
- **Roles enforced on the server:** every route that changes data requires the admin role. A test walks *all* routes to make sure none was forgotten.
- Passwords hashed; no default passwords (`install.sh` asks for the first admin's password).
- No secrets or real names in the repository; the example school is anonymized (names generated, rooms renamed, days permuted).
- Errors show a Bulgarian error page, not a stack trace.
- Behind a reverse proxy with HTTPS; the app only trusts the proxy headers when configured to.

---

## 8. Testing (also scored)

- **Checker:** for every rule type, a valid timetable passes and a hand-built bad one is caught with the right violation.
- **Solver:** every seed school is solved and then run through the checker, expecting zero hard violations. An impossible school must come back `INFEASIBLE` with the right rules named.
- **AI:** tested on recorded responses, so CI never calls the paid API.
- **App:** pages, roles, CSRF, import/export, PDF, migrations, on PostgreSQL in CI.
- CI on GitHub Actions: ruff lint + format check + all tests; a PR can't be merged if they fail.

---

## 9. Demo script (≈ 5 min, rehearse it)

1. Log in as **admin**. Home dashboard.
2. Show data already imported from the example school (aSc import). Mention Excel import.
3. Type a rule in Bulgarian, e.g. *„Учителят по физика не може в петък.“* The AI proposes it with an „ИИ“ badge → **Потвърди**.
4. **Генерирай разписание** → job runs in the background → timetable in a few seconds → checker report: **0 нарушения**.
5. Open a class grid and a teacher view; show dark mode.
6. Add a rule that contradicts another one → solve → the app **names the clashing rules** → relax them → solve again.
7. Minimal-change re-solve: add a small rule to the published timetable and show how few lessons moved (diff).
8. Export to Excel/PDF; show the public QR page on a phone.
9. Log in as **preglad** (read-only) to show the second role.

Backup if Wi-Fi or the AI fails: add the same rule through the form (the app works with the AI off), the offline laptop copy, and the recorded video.

---

## 10. Honest limitations (say them before the jury does)

- One school per installation (by design: each school hosts its own copy).
- Calendar-free: one repeating week (or A/B weeks). No holidays or term dates; the timetable itself doesn't need them.
- No substitutions for absent teachers yet.
- The teacher norm (хорариум) is a check in the report, not a solver rule: a teacher's hours come from the curriculum assigned to them.
- The AI needs an API key and internet; without it the admin uses the forms.
- Bulgarian school regulations are **not** hard-coded: every rule is entered by the school. We didn't want to invent regulations; the open questions are listed in `TECH_STACK.md` §15.

---

## 11. Likely jury questions with short answers

**"Why not let ChatGPT just make the timetable?"**
It can't guarantee zero clashes and can't explain impossible requests. We use it only to understand text; a solver with mathematical guarantees places the lessons, and a person confirms every rule.

**"What if the AI misunderstands?"**
Nothing it proposes is used until the admin confirms it. The admin sees the original sentence next to the proposed rule.

**"What happens when the rules can't all be satisfied?"**
CP-SAT proves it's impossible and returns the smallest set of the school's rules that clash (via assumption literals). We show those rules and offer to turn them into preferences.

**"How do you know the timetable is correct?"**
A separate checker, written from the specification and never importing solver code, re-checks every rule after every solve.

**"Why is it a distributed application?"**
Separate web, worker, database and queue services; long solves run on the worker, not in the web request; the same stack deploys to a VPS or a school server with one script.

**"How big a school can it handle?"**
The 27-class, 648-lesson example school solves to optimal in about 3 s. The time limit is 60 s and the best solution found is always kept.

**"Is student/teacher data safe?"**
Each school hosts its own copy. The only data that leaves the server is the rule text plus names sent to the AI when the admin uses it; that's stated in the README, and the AI can be switched off.

**"What did each of you do?"** (prepare an honest split)
Backend (Boyan): infrastructure, database, auth, solver, background jobs, AI. Frontend (Hristian): pages, checker, demo data, exports, slides. Agree on the exact wording together; the jury may ask either of you about any part.

**"Did you use AI to write the code?"**
Decide the answer together before the defense and answer honestly.

**"What's next?"**
Substitutions, more rule types, an optional local AI model, backup/update scripts.

---

## 12. Suggested slide outline

1. Title: SATurn + team + supervisor
2. The problem (days of manual work, every change starts over)
3. The idea: AI understands → solver places → checker proves
4. Architecture diagram (§3)
5. Why these technologies (§4, keep to 5–6 rows)
6. Demo (live, §9)
7. Impossible rules explained (screenshot as backup)
8. Security and tests (§7–8, numbers)
9. Results: numbers (§6)
10. Limitations and next steps (§10)
11. Thank you / QR to the live site

Slide style should match the app: calm, Bootstrap blue, readable in light and dark, correct literary Bulgarian.

---

## 13. Still to confirm before the slides are final

- [ ] 2026 regulation: defense length, slide limit, required documentation structure (TEAM-01).
- [ ] Live URL and test accounts (needs the VPS, BE-02/BE-10).
- [ ] Final solve times on the deployed build.
- [ ] Whether PR #112 (хорариум) is merged.
- [ ] Team split and the "did you use AI" answer, agreed together.

# 9. За разработчици

[← Съдържание](README.md)

Правилата за работа по кода са в [`CLAUDE.md`](../CLAUDE.md), а техническият план в [`TECH_STACK.md`](../TECH_STACK.md).
Тази глава ги обобщава и добавя практически указания.

- [9.1. Локална среда](#91-локална-среда)
- [9.2. Команди](#92-команди)
- [9.3. Структура на хранилището](#93-структура-на-хранилището)
- [9.4. Адреси на приложението](#94-адреси-на-приложението)
- [9.5. Начин на работа](#95-начин-на-работа)
- [9.6. Как се добавя нов вид ограничение](#96-как-се-добавя-нов-вид-ограничение)
- [9.7. Примерни данни и инструменти](#97-примерни-данни-и-инструменти)
- [9.8. Правила за интерфейса](#98-правила-за-интерфейса)

---

## 9.1. Локална среда

Нужни са Python 3.12 и [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/protoknight12/timetable-solver.git
cd timetable-solver
uv sync                  # създава .venv с всички зависимости (и тези за разработка)
uv run pytest            # всички тестове (на SQLite в паметта)
```

За да пуснете приложението без Docker, създайте `.env`:

```ini
SECRET_KEY=dev
DATABASE_URL=sqlite:///saturn.db
REDIS_URL=
SOLVE_INLINE=true
LLM_ENABLED=false
DEMO_ADMIN_PASSWORD=adminadmin
DEMO_VIEWER_PASSWORD=viewerviewer
```

```bash
uv run flask --app app db upgrade
uv run flask --app app seed seeds/demo_school.json   # създава и профилите admin и preglad
uv run flask --app app run --debug                   # http://127.0.0.1:5000, презарежда при промяна
```

`--debug` изключва изискването за HTTPS на бисквитката, затова входът работи на `http://127.0.0.1:5000`.

**С Docker:** `cp .env.example .env`, попълнете `SECRET_KEY` и `POSTGRES_PASSWORD`, после `docker compose up --build`
(http://localhost:8000, без автоматично презареждане).

**Тестове на PostgreSQL** (както в CI): задайте `TEST_DATABASE_URL=postgresql+psycopg://...` преди `uv run pytest`.
PDF тестовете се пропускат, ако WeasyPrint не може да се зареди; с `REQUIRE_PDF=1` (както в CI) липсата е грешка.

## 9.2. Команди

| Команда | Какво прави |
|---|---|
| `uv sync` | Инсталира зависимостите |
| `uv run pytest` | Всички тестове |
| `uv run ruff check . && uv run ruff format --check .` | Проверка на стила (CI пуска и двете) |
| `uv run ruff format .` | Форматира кода |
| `uv add <пакет>` | Нова зависимост (качват се заедно `pyproject.toml` и `uv.lock`) |
| `uv run flask --app app db upgrade` | Прилага миграциите |
| `uv run flask --app app db migrate -m "..."` | Нова миграция след промяна в моделите (само след съгласие, правило 7) |
| `uv run flask --app app seed [ФАЙЛ] [--reset]` | Зарежда примерно училище (по подразбиране `seeds/example_school.json`) и демо профилите |
| `uv run flask --app app create-user ИМЕ --role admin\|viewer [--password-env VAR]` | Нов профил |
| `uv run flask --app app routes` | Всички адреси |
| `uv run python -m schema` | Генерира наново `schema/constraint.schema.json` след промяна в `schema/` |
| `uv run python -m tools.seed_to_contract` | Обобщение и проблеми на примерното училище |
| `uv run python -m tools.bench_solver [--constraints] [секунди…]` | Измерване на алгоритъма върху примерното училище (не се пуска в CI) |
| `docker compose up --build` | Целият стек: web, worker, db, redis |

## 9.3. Структура на хранилището

| Път | Съдържание |
|---|---|
| `app/__init__.py` | Фабрика на приложението: настройки, разширения, регистрация на blueprints |
| `app/models.py` | Модели на SQLAlchemy (единственото място, което познава базата) |
| `app/auth.py` | Вход, роли (`admin_required`), CSRF, ограничения на заявките, команда `create-user` |
| `app/cli.py` | Команда `seed` и изчистване на данните на училището |
| `app/forms.py` | Формуляри за данните (съобщенията са на български) |
| `app/constraint_forms.py` | По един формуляр за всеки вид ограничение и обща проверка `constraint_problem` |
| `app/solver_input.py` | База → `SolverInput` (само потвърдените ограничения) |
| `app/jobs.py` | Фоновата задача: вход → алгоритъм → проверка → запис |
| `app/llm.py`, `app/llm_resolve.py` | ИИ: заявка, проверка и запис на предложенията; разпознаване на имена, часове и дни |
| `app/importing.py`, `app/asc_import.py` | Импорт и износ на шаблона; импорт от aSc |
| `app/history.py` | Как е направена една версия и разлика между две версии |
| `app/solve_text.py` | Български текстове за състоянията и нарушенията |
| `app/api_spec.py` | Описание OpenAPI на REST API |
| `app/class_names.py`, `app/room_types.py`, `app/subject_colors.py` | Имена на класове и кодове, видове стаи, цветове на предмети |
| `app/errors.py` | Страници за грешки на български |
| `app/routes/` | Адресите: `pages`, `data`, `importing`, `constraints`, `solve`, `workload`, `users`, `public`, `api` |
| `app/templates/`, `app/static/` | Jinja2 шаблони, CSS и малко JavaScript (тема, плъзгане на уроци, печат, прозорец за ограничения) |
| `schema/contract.py` | Договорът: `SolverInput`, `SolveResult`, проверка `problems()` |
| `schema/constraints.py` | Pydantic модели на 14-те вида ограничения; от тях се генерира JSON схемата |
| `solver/` | CP-SAT модел и бърза проверка за претоварване |
| `checker/` | Независимата проверка |
| `seeds/` | Анонимизирани примерни училища |
| `migrations/` | Миграции на Alembic |
| `tools/` | Превръщане на данни, анонимизиране, измерване |
| `tests/` | `app/`, `schema/`, `solver/`, `checker/`, `seeds/` |
| `install.sh`, `Dockerfile`, `docker-compose.yml` | Инсталиране и пускане |

## 9.4. Адреси на приложението

„Вход“ = нужен е вход с всяка роля. „Админ“ = нужна е роля Администратор (зрителят получава 403, невлезлият се
пренасочва към входа).

| Адрес | Метод | Достъп | Какво прави |
|---|---|---|---|
| `/` | GET | всички | Начална страница |
| `/login`, `/register` | GET, POST | всички | Вход, регистрация (роля Преглед) |
| `/logout` | POST | вход | Изход |
| `/data/<вид>/` | GET | вход | Списък (`teachers`, `subjects`, `rooms`, `classes`, `periods`, `courses`) |
| `/data/<вид>/new`, `/<id>/edit`, `/<id>/delete` | GET, POST | админ | Добавяне, редактиране, изтриване |
| `/data/classes/<id>/groups/new`, `/data/groups/<id>/edit`, `/delete` | GET, POST | админ | Групи |
| `/data/courses/<id>/parts/new`, `/data/parts/<id>/edit`, `/delete` | GET, POST | админ | Части на учебен план |
| `/data/classes/<id>/public` | POST | админ | Публична страница: `action=on\|off\|new` |
| `/data/classes/qr` | GET | админ | QR кодове |
| `/data/import/` | GET, POST | админ | Импорт от шаблон |
| `/data/import/asc` | POST | админ | Импорт от aSc |
| `/data/import/template.xlsx`, `/export.xlsx` | GET | админ | Празен шаблон, текущи данни |
| `/constraints/` | GET | вход | Списък с ограничения |
| `/constraints/new`, `/new/<вид>` | GET, POST | админ | Формуляр (прозорец с HTMX или цяла страница) |
| `/constraints/new/<вид>/many` | POST | админ | Няколко ограничения наведнъж (само с HTMX) |
| `/constraints/<id>/status`, `/<id>/delete` | POST | админ | Потвърждаване / отхвърляне, изтриване |
| `/constraints/propose` | POST | админ | Текст → предложения от ИИ |
| `/solve` | POST | админ | Пускане на изграждане |
| `/jobs/<id>/status` | GET | вход | Състояние като JSON |
| `/jobs/<id>/card` | GET | вход | Карта за HTMX (проверява се на 2 секунди) |
| `/jobs/<id>/relax`, `/publish`, `/restore` | POST | админ | Правилата в предпочитания, публикуване, възстановяване |
| `/timetable`, `/overview` | GET | вход | Разписание, общ разпис (`?job=`, `?class=`, `?teacher=`, `?room=`, `?week=A\|B`) |
| `/timetable.xlsx`, `/timetable.pdf`, `/overview.pdf` | GET | вход | Изтегляне |
| `/history`, `/history/<id>` | GET | админ | История, сравнение (`?against=`, `?class=`, `?teacher=`, `?by=teacher`) |
| `/lessons/<id>/move` | GET, POST | админ | Преместване на урок |
| `/lessons/<id>/pin`, `/unpin` | POST | админ | Закачане и откачане |
| `/workload`, `/workload/<id>`, `/workload.xlsx` | GET | вход | Натовареност |
| `/admin/users/`, `/new`, `/<id>/role` | GET, POST | админ | Потребители |
| `/p/<код>` | GET | всички | Публична страница на клас |
| `/api/v1/...` | GET | вижте [7](07-api.md) | REST API |

## 9.5. Начин на работа

- **Задачите** са GitHub issues с имена `BE-xx` (бекенд, Боян), `FE-xx` (фронтенд, Християн) и `TEAM-xx` (общи), в
  проекта „SATurn backlog“.
- **Клон за всяка задача:** `be-05-login`, `fe-04-checker`, `team-03-schema`.
- **Всяка промяна минава през заявка за сливане (PR)** в `main`, с `Closes #<номер>`. Проверката `test` в CI трябва
  да мине (клонът е защитен). Преди PR пуснете стила и тестовете.
- **Малки и ясни commit-и.** Не се пренаписват несвързани файлове.

**Основни правила** (пълният текст е в `CLAUDE.md`):

1. ИИ никога не нарежда часове. Изходът му е само предложено ограничение, което чака потвърждение.
2. `solver/`, `checker/` и `schema/` не внасят Flask, SQLAlchemy и нищо от `app/`.
3. `checker/` не внася и не копира код от `solver/`.
4. Тестовете са в същата промяна като кода. Промяна в алгоритъма без тест на проверката не е завършена.
5. Без тайни, истински имена и истински данни на училище в хранилището.
6. Нов вид ограничение, таблица или зависимост само с изказана причина.
7. Промени във формата на ограниченията и в схемата на базата само след съгласие на двамата разработчици.
8. Не се измислят български училищни наредби. Неясното се записва като предположение в коментар и като въпрос.
9. **Замразяване на функционалностите: 26.11.2026.** След тази дата само поправки, подобрения и тестове.
10. Текстът за потребителя е на книжовен български. Кодът, коментарите и commit-ите са на английски.

## 9.6. Как се добавя нов вид ограничение

След турнира (заради замразяването) и само след съгласие на двамата разработчици (правило 7). Списък:

1. `schema/constraints.py`: модел на параметрите, модел на ограничението с `type: Literal[...]` и добавяне към
   `ConstraintModel`.
2. `schema/contract.py`: добавяне в `CONSTRAINT_TYPES` и проверка на препратките в `problems()`; при нужда
   увеличаване на `CONTRACT_VERSION`.
3. `uv run python -m schema`, за да се обнови `schema/constraint.schema.json` (от нея се захранва и ИИ).
4. `solver/__init__.py`: функция `_add_<вид>` (твърдо и като предпочитание) и запис в `_ADDERS`. Помислете за
   седмиците A/B. Ако видът засяга натоварването на учител или клас, вижте и `solver/diagnose.py`.
5. `checker/core.py`: самостоятелна проверка, написана **по спецификацията**, без да се гледа кодът на алгоритъма.
   Нов ключ на съобщение → текст в `app/solve_text.py`.
6. `app/constraint_forms.py`: формуляр, запис във `FORMS` и (ако има цел) в `TARGETS`.
7. `app/routes/constraints.py`: описание на български в `describe`.
8. `app/llm.py`: описание на вида и типични български изрази в системния текст.
9. Тестове: schema (приема/отхвърля), solver (спазва се), checker (вярно минава, грешно се хваща), формуляр, ИИ
   със записан отговор.
10. Документация: [5. Справочник на ограниченията](05-constraints.md).

## 9.7. Примерни данни и инструменти

| Файл | Предназначение |
|---|---|
| `seeds/*.json` | Примерни училища (вижте [2.5](02-quick-start.md#25-налични-примерни-училища)). Всички са анонимизирани. |
| `tools/seed_to_contract.py` | Превръща файл от `seeds/` в `SolverInput`. Използва се от `flask seed` и тестовете. |
| `tools/asc_import/extract_asc_pdf.py` | Извлича уроците от PDF разписание на aSc. Изходът съдържа истински имена и **никога** не се качва. |
| `tools/asc_import/build_seed.py` | Превръща извлеченото в анонимизиран `seeds/example_school.json`. |
| `tools/asc_import/anonymize.py` | Втора анонимизация: стаи, звънец, разменени дни. |
| `tools/make_demo_seeds.py` | Генерира измислените демонстрационни училища. |
| `tools/bench_solver.py` | Измерва алгоритъма при различни лимити на времето. |

## 9.8. Правила за интерфейса

Накратко (подробно в `CLAUDE.md`, раздел „Visual style“):

- Страниците се рендират на сървъра с Jinja2 и наследяват `app/templates/base.html`. Bootstrap 5.3 и HTMX идват от CDN.
- Само цветовете на темата на Bootstrap (`bg-body-tertiary`, `text-body`…), без твърдо зададени цветове, за да
  остане тъмната тема четима. Всяка страница се проверява в светла и тъмна тема.
- Едно `h1` на страница, формулярите в `card shadow-sm`, полетата с `form-floating` и български етикет.
- Иконите са само Bootstrap Icons и винаги до текст.
- Всеки формуляр, който променя данни, съдържа CSRF токена.
- Връзките се правят с `url_for`, никога с твърдо зададен адрес.
- Нова страница = шаблон + адрес в `app/routes/` + тест в `tests/app/`.

---

[← 8. Техническа документация](08-technical.md) · [Следваща глава: 10. Чести въпроси →](10-faq.md)

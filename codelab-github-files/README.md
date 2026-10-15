# CodeLab - Smart Campus Lab Assessment System
*Write Your Code. Prove Your Skills.*

A browser-based C++ lab platform: students write code, a **real compiler service (Judge0-compatible)** runs it, test cases judge it, teachers review submissions. This is a college **hackathon demo**, not an official university system. It uses demo accounts and sample data only.

## What changed from the first version (audit + fixes)
| # | Problem found in the old code | Fix |
|---|---|---|
| 1 | Comment-stripping regex with DOTALL let `//` swallow the rest of the file; string literals containing `//` were mishandled | Lexical scanner (`similarity.py`) that understands strings, chars, line/block comments; unit-tested |
| 2 | Judge0 called with `wait=true` only (not available on every host) and no retries | Async token + polling with bounded backoff, retry on 429/502/503, optional single-call `wait` mode with automatic fallback |
| 3 | One test failing because the SERVICE failed could be saved as a wrong answer | Separate states; service failures become `evaluation_error`, never `evaluated_failed` |
| 4 | Evaluation ran inside the web request (could time out) | Background evaluation + browser polling; restart recovery of unfinished jobs |
| 5 | Double-click could create duplicate submissions | Idempotency token + database-level unique index allowing only one active evaluation per student/assignment |
| 6 | Per-test results were one JSON blob | `test_results` table (snapshots of input/expected/actual, status, time) |
| 7 | Secret key regenerated on every restart; no login throttling; no rate limits; no security headers | Stable key (file in dev, required env var in production), login throttle, run/submit rate limits, CSP and other headers, secure cookies in production |
| 8 | Student could read other students' data only by luck of route design | Ownership enforced inside SQL queries and tested |
| 9 | Viva module | **Removed completely** (code, table, routes, docs) |
| 10 | SQLite only, wiped on free hosts | PostgreSQL support (`DATABASE_URL`) + honest persistence notes |
| 11 | No automated tests | 60 unit/integration tests + 26 browser checks |
| 12 | Hidden test data could be exposed through reports/errors | Reports show outcomes only; tests assert known hidden strings never reach any student-facing response |

## Folder structure
```
app.py            Flask app factory and all routes
config.py         environment-based settings
db.py, schema.py  SQLite/PostgreSQL layer and tables
executor.py       Judge0 adapter (submit, poll, retry, error mapping)
evaluation.py     output comparison, per-test results, status state machine
similarity.py     C++ token scanner + similarity review aid
security.py       rate limiter, login throttle
init_db.py        create tables / demo data / deliberate reset
check_executor.py verify YOUR Judge0 setup with real programs
wsgi.py           production entry point
templates/ static/   UI (student workspace, teacher dashboard, report)
tests/            unit tests, Judge0 test double, C++ fixtures, browser test
render.yaml, Procfile, .env.example, requirements*.txt
```

## Windows setup (local)
```
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python init_db.py
python app.py
```
Open http://127.0.0.1:5000 . **Demo logins** (public, NOT for production): `student01 / Student@123`, `student02 / Student@456`, `teacher01 / Teacher@123`.
Without a Judge0 service the app still works but says "Code execution is not configured" - it never fakes compiler output.

## Enable real C++ compilation (Judge0)
1. **Hosted (quickest):** RapidAPI -> "Judge0 CE" -> subscribe. In `.env`:
   `JUDGE0_URL=https://judge0-ce.p.rapidapi.com`, `JUDGE0_API_KEY=<key>`, `JUDGE0_API_HOST=judge0-ce.p.rapidapi.com`.
2. **Self-hosted:** run Judge0 CE yourself (Docker, Linux server) and set `JUDGE0_URL=http://server:2358` (+ `JUDGE0_API_KEY` if you configured an auth token; header name in `JUDGE0_AUTH_HEADER`).
3. **Verify (do this before trusting anything):** `python check_executor.py`. It lists the C++ languages your service offers, checks `JUDGE0_LANGUAGE_ID` (default 54) exists, and runs a stdin program, a compile error and a time-limit program on the REAL service. Teachers can also press "Test connection" on the *Execution service* page.
4. **Quota warning (checked Oct 2026):** the free RapidAPI Judge0 CE plan listed **50 submission calls/day**; paid tiers listed 500 / 2,000 / 5,000 per day. Each test case costs one POST plus at least one poll, so one student submission with 4 tests uses roughly 8-12 calls: the free plan covers only a handful of submissions per day. Set `JUDGE0_USE_WAIT=1` to use ONE call per test if your host allows `wait=true` (the app falls back automatically if not). For a real class, use a paid tier or self-host. Quotas change; re-check on the provider page.

## Environment variables
See `.env.example` (all documented). Required in production: `APP_ENV=production`, `SECRET_KEY`, `DATABASE_URL` (PostgreSQL), `JUDGE0_URL` (+ key/host). Secrets are only read on the server and never rendered into pages or error messages.

## Deploy (free/low cost)
Suggested: **Render free web service + free Neon PostgreSQL**.
1. Put this folder in a GitHub repo (`.env`, `*.db` are git-ignored).
2. Neon: create a free Postgres project, copy the connection string (keep `?sslmode=require`).
3. Render: New -> Blueprint -> select the repo (`render.yaml`). Fill `DATABASE_URL`, `JUDGE0_URL`, `JUDGE0_API_KEY`, `JUDGE0_API_HOST` in the dashboard (`SECRET_KEY` is generated).
4. After the first deploy open the Render shell once: `python init_db.py` (creates tables + demo data; existing data is never erased).
5. Open `https://<yourapp>.onrender.com/health` then log in, then run the teacher "Test connection".
Provider facts (checked Oct 2026 on Render's docs): free web services **spin down after 15 minutes idle (about 1 minute to wake)**, may restart at any time, and share monthly free instance hours; **Render's own free Postgres expires after 30 days with no backups** - that is why the blueprint does not use it. Neon's free tier is reported to persist, but verify its current limits and backup options yourself. SQLite on a free host's disk is **ephemeral** (wiped on restart/redeploy): use PostgreSQL for anything you want to keep.
Run command used in production: `gunicorn wsgi:app --workers 1 --threads 4 --timeout 120` (one worker because rate limits and the evaluation thread pool are in memory). On startup `wsgi.py` re-queues submissions that were waiting.
**Troubleshooting:** *"Code execution is not configured"* -> `JUDGE0_URL` empty. *auth error* -> wrong key/host. *bad_request* -> wrong `JUDGE0_LANGUAGE_ID` (run `check_executor.py`). *evaluation_error submissions* -> service outage or quota; teachers press **Re-evaluate**. *First page load slow* -> Render cold start. *Logged out after redeploy* -> `SECRET_KEY` not set/stable. *Stuck "evaluating"* -> reaped after `STALE_EVAL_MINUTES` (10).

## Policies (as implemented)
- **Statuses:** `submitted -> evaluation_pending -> evaluating -> evaluated_passed | evaluated_failed | evaluation_error` (re-evaluation returns to pending). The code is saved BEFORE evaluation. Compile errors are `evaluated_failed` (a real student result). Timeouts/runtime errors fail only that test. A service problem anywhere stops evaluation as `evaluation_error` with partial results kept, and is never shown as a wrong answer.
- **Output comparison (per assignment):** both modes convert CRLF/CR to LF. *Lenient* (default) also strips trailing spaces on each line and trailing blank lines. *Exact* keeps all spaces (one optional final newline). Internal characters and spacing are never changed; case matters; empty output equals only empty output. Tests that print prompts such as `Enter a number:` fail on purpose.
- **Hidden tests:** stored server-side only. Student pages/APIs/reports receive only passed/failed per hidden test. Teachers see everything. Automated tests search every student-facing response for known hidden strings.
- **Deleting assignments:** with no submissions -> deleted. With submissions -> **archived** (hidden from students, evidence kept, can be restored). Editing tests does not re-evaluate old submissions (teacher can press Re-evaluate).
- **Similarity (review aid, NOT proof of cheating):** the scanner drops comments, whitespace and `#include` lines, treats string/char literals correctly, then compares token sequences with `difflib`. Two scores: *exact tokens* and *structure* (variable names/literal values replaced by placeholders, so renaming variables does not hide copying). Flag when structure >= `SIM_THRESHOLD` (0.85). Submissions under `SIM_MIN_TOKENS` (25) or still equal to the starter template are skipped. Only each student's latest submission is compared; a submission is never compared with itself. No AI/ML or AST analysis is claimed. Teachers inspect pairs side by side with a line diff; nothing is penalised automatically.
- **Activity log:** assignment opened, code run, submission saved, evaluation completed/requested, submission reviewed. It records actions on the platform; it does not prove who wrote each line.
- **Paste deterrent:** optional (`PASTE_DETERRENT=1`), a deterrent only; typing, keyboard navigation and Tab behaviour are preserved (Esc then Tab leaves the editor).
- **Late submissions:** deadlines are displayed but not enforced.

## Tests (actual results from my build environment)
```
python -m unittest discover -s tests -t .        # 60 tests: 57 ran and passed, 3 skipped (live-service suite)
LIVE_TEST_DOUBLE=1 python -m unittest tests.test_live_cpp   # 3 passed against the test double
python tests/e2e_browser.py                      # 26/26 Playwright (headless Chromium) checks passed
JUDGE0_URL=... JUDGE0_API_KEY=... python -m unittest tests.test_live_cpp -v   # RUN THIS against your real service
```
Tests are plain `unittest` (pytest runs them too: `pytest tests`). Mocked tests (`ScriptedExecutor`) are labelled in the code. `tests/fake_judge0.py` is a **test double that speaks the Judge0 protocol and compiles with local g++ 13.3** - it is NOT Judge0 and NOT sandboxed; never expose it.

## Evidence report
| | Feature | A. In source | B. Automated tests | C. Real Judge0 | D. Real browser | E. Restart/redeploy |
|---|---|---|---|---|---|---|
| 1 | Login/logout, sessions, roles, CSRF, throttle | yes | yes | n/a | yes (login, 403) | partly* |
| 2 | Assignment CRUD, archive policy | yes | yes | n/a | create tested | - |
| 3 | Run (real compile) | yes | yes (test double) | **NO** | yes (vs double) | - |
| 4 | Submit + per-test evaluation + states | yes | yes (mock + double) | **NO** | yes (vs double) | - |
| 5 | Hidden-test protection | yes | yes | n/a | yes | - |
| 6 | Service outage != wrong answer | yes | yes | **NO** | yes | - |
| 7 | Poll/retry/timeout/auth/malformed handling | yes | yes (fault injection) | **NO** | - | - |
| 8 | Persistence | yes | yes (2nd app on same file, crash recovery) | n/a | refresh tested | real process restart/redeploy **NOT tested** |
| 9 | Similarity + compare + diff | yes | yes | n/a | not exercised | - |
| 10 | Report + print | yes | yes | n/a | PDF generated | - |
| 11 | Teacher filters, dashboard numbers | yes | yes | n/a | yes | - |
| 12 | Monaco editor | yes | no | n/a | **NO** (CDN unreachable here; textarea fallback tested) | - |
| 13 | PostgreSQL | yes | **NO** (no Postgres here) | n/a | - | - |
| 14 | Deployment (Render/Neon) | files written | **NO** | - | - | **NOT deployed** |
*Restart is simulated with a second app instance on the same SQLite file.
**Partially implemented / not done:** per-student chart history (summary bars and tables only), Monaco and other browsers/phones untested, rate limiting is per process (not shared), login throttle keyed by IP+username, no migrations tool (schema is create-if-missing), no automated PostgreSQL run, memory-limit detection depends on what your Judge0 reports (`bad_alloc`/limit heuristics), the test double reports a fixed "GCC 9.2.0" label whereas it really uses g++ 13.3.

## Manual acceptance checklist (do on the DEPLOYED site)
1. `/health` shows ok. 2. Teacher "Test connection" says Working and shows the compiler name. 3. Student Run: Hello World prints. 4. Run with a syntax error shows compiler text. 5. Submit a correct solution -> `evaluated_passed`; refresh -> history still there. 6. Submit one that prints a prompt -> failed. 7. Open the student page source: no hidden values. 8. Student URL `/teacher` -> 403; another student's `/report/<id>` -> 404. 9. Teacher: filter submissions, open one, see hidden test rows. 10. Two students submit near-identical code -> flagged pair appears. 11. Print a report to PDF. 12. Temporarily break `JUDGE0_API_KEY` -> Run/Submit say service problem, submission preserved, Re-evaluate works after fixing. 13. Redeploy -> data still there (PostgreSQL). 14. Keyboard: Tab to Run, Ctrl+Enter runs.

## Known limitations and remaining risks
Demo passwords are public. No password reset or user management UI. In-memory rate limits reset on restart. Free Judge0/Render tiers are small (see above). Output comparison is exact text (no floating-point tolerance). CSP was written for Monaco from cdnjs but not browser-tested with Monaco loaded. Evaluation threads run inside the web process. Backups: SQLite/Postgres backups are your responsibility (`pg_dump`/provider tools); Render free Postgres has none. Unit tests passing does not make the system production-secure: it has not had an independent security review.

## 5-minute demo script
1. Teacher login -> *Execution service* -> Test connection (shows compiler). 2. Student01 -> Assignments -> open *Sum of Two Numbers*. 3. Type a solution that prints a prompt -> Submit -> failed, explain strict output. 4. Fix it -> Run with `20 22` -> `42` -> Submit -> hidden tests pass. 5. Student02 submits the same code with renamed variables. 6. Teacher dashboard: counts and bars -> Similarity page -> flagged pair -> side-by-side. 7. Open the submission -> hidden test rows visible only to teacher -> activity timeline. 8. Print the report. 9. Stop the compiler key -> Submit -> evaluation_error (not a wrong answer) -> restore -> Re-evaluate.

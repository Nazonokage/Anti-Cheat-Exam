# Anti-Cheat Exam App

Server-authoritative, one-question-at-a-time classroom exam system built with
Django + Tailwind + SQLite, per `plan.md`. Tested end-to-end (see below).

## Quickstart

```bash
python3 -m venv venv && source venv/bin/activate      # optional but recommended
pip install -r requirements.txt

python3 manage.py migrate
python3 manage.py createsuperuser                      # this account is your first "teacher"

python3 manage.py import_exam data/sampletopic.json --teacher <your_username>
# (omit --teacher to default to the first superuser)

python3 manage.py runserver 0.0.0.0:8000                # 0.0.0.0 so classmates on the LAN can reach it
```

Then:
1. Go to `http://<your-ip>:8000/admin/`, log in, open **Exams**, and tick
   **is_active** on the imported exam (or select it and use the *Activate
   selected exams* action). Exams import as inactive by default.
2. On the same exam's edit page, scroll to the **Students** inline and add
   each student's name and a passcode you choose (there's no student
   self-registration — you control the roster and who can get in).
3. Students go to `http://<your-ip>:8000/`, pick their exam, pick their
   name from the roster dropdown, and type the passcode you gave them.
4. You can watch them live at
   `http://<your-ip>:8000/teacher/monitor/<exam_id>/` (exam id shown in the
   admin list).
5. When done, go back to the Exams list in admin, select the exam, and use
   **Export results to CSV**.

You can also import more exams straight from the admin UI: **Exams → Import
JSON** (top-right button on the changelist page), using the same JSON shape
as `data/sampletopic.json`.

### Styling uses the Tailwind CDN

`base.html` pulls in `@tailwindcss/browser@4` from `cdn.jsdelivr.net` and
defines the emerald palette inline via a `<style type="text/tailwindcss">`
block with `@theme`. No Node/npm tooling, no build step, no `package.json`
— it's plain Django + a CDN script, matching the rest of the stack.

**Trade-off to know about:** this means the app needs internet access to
load its styling (the CDN script itself). If you're deploying on a school
LAN with no outside internet, the pages will still work but will render
unstyled until that script loads. If that ever becomes a problem, the fix
is to self-host a built Tailwind CSS file instead of the CDN script — just
say the word and it can be swapped back.

## Newer additions (this round)

- **Per-student question randomization**: each student gets their own
  shuffled question order (`Submission.question_order`), set once at first
  login. Makes "question 5 is X" harder to share between students taking
  the same exam. All the timer/auto-skip/review logic now resolves
  questions through this per-student order (`views.py::_get_ordered_questions`)
  instead of the exam's fixed natural order.
- **Score summary shown after submission**: the "Exam Submitted" screen
  now shows score / total, percentage, and questions answered
  (`views.py::_score_summary`), plus a note if tab-switch attempts were
  recorded.
- **Keyboard shortcuts** on the question/review screens: number keys
  (1-9) or letter keys (a, b, c...) pick a choice, Enter submits,
  Ctrl/Cmd+S skips. Typing in the identification text box is left alone
  except Enter. Doesn't interfere with the anti-cheat listeners — added as
  its own independent block in `exam.js`.
- **Log-only violation reporting** via a new `/report-violation/` endpoint
  and `reportViolation`'s sibling `logViolation` in `exam.js`:
  - Copy attempts log the actual selected text (truncated to 200 chars) so
    a teacher can see what was being copied, not just that a copy happened.
  - Paste and cut attempts are logged too.
  - Prolonged idle (30s with no mouse/keyboard/touch activity) is logged.
  - **Deliberately separate from the tab-switch escalation** — these are
    informational only and do NOT count toward the 6-warning /
    3-lock / auto-close schedule, which stays scoped strictly to
    tab-switch/window-blur as originally specified. Verified: hitting
    `/report-violation/` three times mid-exam left `tab_attempts` at 0 and
    the very next real tab-switch still started at attempt 1, not 4.
- **Live teacher monitor** now shows a Violations column (total count
  across all violation types, not just tab-switch) with a pulsing dot for
  recent activity in the last 30s, plus a live-ranked **Score** column
  (correct/answered out of the exam's total) — rows are sorted most-correct
  first (ties broken by answered-so-far) on every 5s poll, so the class
  ranking updates in real time as students progress.
- **CSV export** gained `Score %`, `Total Violations`,
  `Most Common Violation Type`, and `Suspiciously Fast Answers (<3s)`
  columns — the last one flags answers submitted in under 3 seconds as a
  signal worth a manual look, not an automatic penalty.
- Answers now record `time_spent_seconds` (server-computed elapsed time),
  which powers the fast-answer CSV flag above.

## What's implemented

- Roster-based sign-in: students never self-register. Teachers add each
  student's name + a passcode per exam in Django Admin (inline on the Exam
  page, or the standalone Student list). The login page is two dropdowns
  (exam, then name) plus a passcode field — see `core/models.py::Student`
  and `core/views.py::login_view`.
- Django models: `Exam`, `Question`, `Choice`, `Student`, `Submission`,
  `Answer` (incl. `question_started_at` and `last_heartbeat` as the plan
  specifies).
- JSON importer (`core/services/importer.py` + `manage.py import_exam` +
  an admin upload form) supporting `multipleChoice`, `boolean`, and
  `identification` question types.
- One-question-per-screen delivery — only the current question is ever sent
  to the browser.
- Server-authoritative adaptive time bank: every remaining-time calculation
  is `seconds_per_question - (now - question_started_at)`, recomputed fresh
  on every request. The client only displays a countdown; it never decides
  when time is up.
- Disconnect-safe by construction: reconnecting just re-derives `elapsed`
  from the same timestamp, so a dropped Wi-Fi connection resumes with
  correct remaining time (or a correct auto-skip if time ran out during the
  gap) with no extra reconciliation logic.
- Auto-skip on timeout, manual "Skip for Now", and a shared review-phase
  time bank that pools unused seconds and lets students revisit
  skipped/unanswered questions before final submission.
- Identification-answer grading matches the plan's normalization rules
  exactly (case/whitespace-insensitive, but genuine misspellings are marked
  wrong — see `core/utils/grading.py`).
- Tab-switch policy: 1–6 warnings, 7th = 10s lock, 8th = 20s lock, 9th =
  30s lock, 10th = exam auto-closed and submitted as-is — regardless of
  whether the student had finished all questions
  (`core/views.py::tab_violation`, enforced server-side). The student sees
  an `alert()` on **every** attempt (1 through 10), not just once locked,
  so they always know exactly where they stand.
- Anti-cheat is layered, redundant on purpose:
  - `document.addEventListener('copy'|'cut'|'contextmenu'|'keydown', ...)`
    in `static/js/exam.js`, registered first and independently of the
    timer/form logic in that file, so a bug elsewhere in the script can't
    silently disable them.
  - **Inline HTML attributes** (`oncopy="return false"`, `oncut`,
    `onpaste`, `oncontextmenu`, plus a `select-none` body class) directly
    on `<body>` in `exam.html`/`review.html` — these work even if the
    external `exam.js` file fails to load or errors out entirely, since
    they're compiled by the browser at parse time, independent of any
    `<script>` tag.
  - Tab-switch/away detection uses both `visibilitychange` and a
    `window blur` fallback, each posting `{type: "tab-switch"|"window-blur"}`
    as JSON to `/tab-violation/` via `reportViolation(type)`. Every
    violation is also written to a `Violation` audit-log row
    (`submission`, `violation_type`, `created_at`) — visible read-only in
    Django Admin — in addition to the running `Submission.tab_attempts`
    counter used for the lock/close schedule.
  - Verified by simulating copy/right-click/F12/tab-switch events against
    the real script in a headless DOM (jsdom), and separately by hitting
    `/tab-violation/` over real HTTP with CSRF enforcement turned on and a
    JSON body — exactly how a browser's `fetch()` sends it — confirming
    all 10 attempts escalate correctly and are logged.
  - These remain deterrents, not guarantees — no page-level JS can fully
    block a determined student from opening devtools via the browser's
    own menu — matching the plan's framing.
- Live teacher dashboard (`/teacher/monitor/<exam_id>/`) polling every 5s,
  separate from Django Admin, with stale-connection highlighting driven by
  `last_heartbeat`.
- Django Admin: JSON import, activate/deactivate/archive actions, CSV
  export (filename includes an export timestamp, e.g.
  `exam_results_20260718_061427.csv`), and normal teacher/staff account
  management (multiple teachers
  supported via ordinary Django staff users).
- Emerald glassmorphism theme via the Tailwind v4 CDN script (no build
  step) matching the plan's palette, applied across login/exam/review/
  locked/teacher-monitor screens.

## Smoke-tested during the build (Django test client)

- Full login → answer → skip → review → final-submit flow, 30/30 correctly
  graded on the sample exam.
- Simulated Wi-Fi drop (sleeping past a question's timeout with no
  requests in flight) → correct auto-skip on reconnect, no time-accuracy
  loss.
- Tab-switch escalation produced exactly the intended schedule: warnings on
  1–6, 10s lock on 7, 20s lock on 8, 30s lock on 9, auto-close (submitted
  as-is, finished or not) on 10.
- Per-student question randomization confirmed different from the exam's
  natural order, with full-length coverage (every question still shown
  exactly once), by walking a student's own `question_order` end to end.
- Log-only violations (copy/paste/idle) confirmed to record correctly
  without touching `tab_attempts` — a subsequent real tab-switch still
  started counting from 1.
- Keyboard shortcuts (number/letter choice selection, Enter-to-submit,
  Ctrl+S-to-skip) and copy-attempt detail logging verified via a headless
  DOM (jsdom) simulation of real key/copy events against the actual script.
- Identification normalization trace matches the plan exactly: "PHP My
  Admin" and "phpmyadmin" both match; "phpmyadmim" is correctly rejected.
- CSV export, Django Admin JSON import, and the live teacher dashboard JSON
  endpoint all verified.

## Notes / things to double check before a real exam

- `exam_system/settings.py` currently has `DEBUG = True` and
  `ALLOWED_HOSTS = ['*']` for easy LAN testing. For a real exam, consider
  setting `DEBUG = False` and restricting `ALLOWED_HOSTS` to your LAN
  subnet/hostnames.
- SQLite + Django's dev server (`runserver`) is fine for the target scale
  (~50 concurrent students on a school LAN) per the plan, but the dev
  server is single-threaded by default — for real use, run it with
  `runserver` and consider `--noreload`, or front it with a small WSGI
  server (e.g. `gunicorn` with a couple of workers) if you want real
  concurrency headroom.
- `SECRET_KEY` in `settings.py` is the Django-generated dev key — fine for
  a closed LAN, but swap it out if this ever leaves your classroom network.

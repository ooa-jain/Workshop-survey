# Job Search → Job Intelligence — survey app

FastAPI + MongoDB Atlas app implementing the three-point survey instrument
(Pre / Post Survey 1 / Post Survey 2). Matches the stack pattern used across the other
juooa.cloud apps: Gunicorn (Uvicorn workers) + Nginx + systemd + Certbot,
MongoDB Atlas for storage.

## Before you deploy — things to check, not assume

1. **Port 8100 is a guess.** I don't have a complete list of every port in
   use across all ~19 hosted projects. Run `sudo ss -tlnp | grep 81` (and
   `8100` specifically) on the VPS before using it — if it's taken, change
   the port in three places: `deploy/job-intelligence-survey.service`,
   `deploy/nginx-job-intelligence-survey.conf`, and `.env` if you reference
   it there.
2. **Domain** `job-intelligence.juooa.cloud` is a guess following the
   existing `<name>.juooa.cloud` pattern (deeksharambh, faculty-research,
   etc.) — point DNS at it, or swap in whatever subdomain you actually want.
3. **SMTP credentials** aren't something I have — you'll need a real SMTP
   account (Gmail app password, SES, SendGrid, whatever you already use)
   before result emails will actually send. Until then, set
   `EMAIL_ENABLED=false` and the app logs what it *would* have sent instead
   of erroring.

## Local test (no real Mongo/SMTP needed)

```bash
pip install -r requirements.txt
pip install mongomock  # test-only, not in requirements.txt
python3 tests/test_scoring.py   # pure scoring logic, 11 checks
python3 tests/smoke_test.py     # full app flow against an in-memory Mongo, 25 checks
python3 tests/test_submit_errors.py  # dead mail server / blank answer / crash on submit
python3 tests/test_bulk_email.py     # a 500-student mail run: one connection, bad addresses
```

To actually run it locally against a real (or Atlas) Mongo:

```bash
cp .env.example .env   # fill in MONGO_URI at minimum; leave EMAIL_ENABLED=false
export $(cat .env | xargs)
uvicorn app.main:app --reload --port 8100
```

Then visit `http://localhost:8100/survey/pre`.

## VPS deployment (first time)

```bash
# on the VPS
cd /var/www
git clone <your-repo-url> job-intelligence-survey
cd job-intelligence-survey

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in MONGO_URI, SMTP_*, ADMIN_PASSWORD, SESSION_SECRET

mkdir -p /var/log/job-intelligence-survey
chown -R www-data:www-data /var/www/job-intelligence-survey /var/log/job-intelligence-survey

sudo cp deploy/job-intelligence-survey.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now job-intelligence-survey
sudo systemctl status job-intelligence-survey   # confirm it's up before touching nginx

sudo cp deploy/nginx-job-intelligence-survey.conf /etc/nginx/sites-available/job-intelligence-survey
sudo ln -s /etc/nginx/sites-available/job-intelligence-survey /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

sudo certbot --nginx -d job-intelligence.juooa.cloud
```

MongoDB indexes (the unique constraint that makes resubmission overwrite
instead of duplicate, and the batch/stage query index) are created
automatically on startup — see `ensure_indexes()` in `app/db.py`. Nothing
extra to run by hand.

## Standard update workflow (matches your other apps)

```bash
cd /var/www/job-intelligence-survey
git pull
source venv/bin/activate && pip install -r requirements.txt   # only if requirements changed
sudo systemctl restart job-intelligence-survey
journalctl -u job-intelligence-survey -f   # tail logs if something looks wrong
```

## How the three surveys connect to each other

There's no anonymous linking code — name and email are captured on every
survey, and **email (lowercased, trimmed) is the join key** across all
three stages for one student within one `WORKSHOP_BATCH`. Resubmitting
with the same email + stage + batch overwrites the previous response
rather than creating a duplicate (tested in `smoke_test.py`).

If you run the workshop again for a different cohort, either point them at
`?batch=2027-final-year` on every survey link, or change `WORKSHOP_BATCH`
in `.env` and restart. The `/admin` dashboard and `/admin/export.csv` are
both scoped to one batch via the same `?batch=` query param.

## URLs to hand out

- Landing (all three, with live lock state): `https://job-intelligence.juooa.cloud/`
- Pre: `https://job-intelligence.juooa.cloud/survey/pre`
- Post Survey 1: `https://job-intelligence.juooa.cloud/survey/post-sameday`
- Post Survey 2: `https://job-intelligence.juooa.cloud/survey/post-week4`
- "Where am I": `https://job-intelligence.juooa.cloud/status`
- Admin (HTTP Basic Auth, credentials from `.env`):
  `https://job-intelligence.juooa.cloud/admin` and `/admin/reminders`
- Shared analysis (no login, one per link): `.../s/<token>` — see below

In practice you only ever hand out the Pre link. Every result page ends
with the three-stage strip showing what's open next, and the Post Survey 2
reminder mail carries its own signed link.

Each submission immediately renders an on-screen result (the arrow-track
dimension chart plus the quadrant position) and emails the same result as
PNG charts embedded via Content-ID, so it displays correctly even in
Outlook desktop rather than depending on inline SVG support.

## Sharing a group's results (`/admin/share`)

The **Share analysis** admin tab publishes one workshop group's first-day
results as a link anyone can open without logging in — for showing a class
its own numbers, or sending them to a department.

Creating a link asks for three things: a **title** shown on the page, a
**group**, and an optional note. A group is everyone whose *Pre* survey was
submitted on a given day — the cohort that sat the workshop together — or
"All groups" for the whole batch. Analysis is scoped to that group only;
other days never appear.

The published page shows, for that group: the Job-Intelligence mean at Pre
and at Post Survey 1, how many rose / fell / changed
quadrant, the arrow field chart (every student's Pre → Post Survey 1 move, plus
the group mean), the trajectory lines, the per-dimension bar chart, the
quadrant population before and after, and a per-student table with each
person's from → to role and score shift.

Two privacy properties hold regardless of settings: **email addresses are
never rendered**, and the page carries `noindex`. Names in the per-student
table are opt-out — untick "show student names" and people are listed as
Student 01, Student 02… Links are unguessable tokens, and **Revoke** on the
admin page kills one immediately (the underlying data is untouched). Each
link shows its view count so you can tell whether it was actually opened.

**Download as PDF** on the shared page opens the browser's print dialog with
a print stylesheet applied — dark stat tiles keep their ink, cards are never
sliced across a page break, and the file is named after the share title. It is
the browser's own PDF writer, not a server-side render, so no extra system
dependency is needed on the VPS. The PDF deliberately stops after the group
charts: the per-student table is screen-only, since a handout for a class
doesn't need a row per person.

**Download as Excel**, next to that table, serves the per-student rows as a
real `.xlsx` from `/s/<token>/students.xlsx` — a *Students* sheet (rank, name,
role at Pre, role at Post Survey 1, both scores, shift) and a *Summary* sheet
(headline means and the quadrant population). It is built from exactly the same
rows the page renders, so an anonymised share produces an anonymised
spreadsheet, and no email address is ever written to either sheet.

This is the one feature that added a dependency — `openpyxl`, a pure-Python
wheel with no system libraries. Run `pip install -r requirements.txt` on the
VPS when you deploy it, or the shared pages will 500 on import.

`BASE_URL` in `.env` is what the copyable link is built from — if it's wrong,
the links you hand out point at the wrong host.

### Survey names

The three stages are shown to students and admins as **Pre**, **Post Survey 1**
(end of the workshop day) and **Post Survey 2** (four weeks on). Those are
display labels only — the stored stage keys are still `pre`, `post_sameday`
and `post_week4`, and the URLs are still `/survey/post-sameday` and
`/survey/post-week4`, so existing data and any links already sent out keep
working. Renaming the labels again means touching templates, not the DB.

## Stage gating -- who can open what, when

Timing is per-student, measured from that student's own Pre submission,
not from a fixed workshop date. The rules live in `app/eligibility.py`
and are enforced in three places: the landing/status cards, the live
`/api/check` call the form makes as soon as a student types their email,
and the POST handler itself. The last one is the one that matters -- the
first two are courtesy, and a student who edits the HTML still can't
submit early.

| Stage | Opens | Closes | Env var |
|---|---|---|---|
| Pre | always | never | -- |
| Post Survey 1 | the instant that student's Pre is saved | `SAMEDAY_WINDOW_HOURS` later (default 14h) | `SAMEDAY_WINDOW_HOURS` |
| Post Survey 2 | `WEEK4_UNLOCK_DAYS` after their Pre (default 30) | `WEEK4_OPEN_DAYS` after that (default 14) | `WEEK4_UNLOCK_DAYS`, `WEEK4_OPEN_DAYS` |

Submitting Pre therefore hands the student Post Survey 1 straight
away: it appears as "Open now" on their Pre result page, and Post Survey 2
appears next to it as "Unlocks in 30 days".

Set `GATING_DISABLED=true` if you need to walk through all three surveys
in one sitting for a demo. Do not leave it on for a live cohort -- it
removes the whole timing design.

Resubmitting a stage that's already done overwrites the previous answers
rather than duplicating them. "Done" is never treated as a lock.

## Sending the Post Survey 2 reminders

`/admin/reminders` splits the cohort into three lists: due a reminder,
still counting down, and finished. "Due" means past their unlock date,
inside the open window, and no Post Survey 2 response yet.

You can send to everyone due at once, or one student at a time. Both
paths run through the same gate, so a stray click cannot mail someone
early. Each mail carries a signed link (HMAC over email + batch +
stage, keyed on `SESSION_SECRET`) that pre-fills their name and email so
they land straight on the questions.

Sends are recorded on that student's Pre document as
`week4_reminder_count` and `week4_reminder_last`, and the table shows
both -- so on a second pass you can see who has already been chased
twice. Nothing is recorded when `EMAIL_ENABLED=false`, because nothing
was actually delivered.

There's no scheduler. This is a deliberate button an admin presses,
which for one workshop a year is simpler to reason about than a cron job
that might silently stop firing. If you later want it automated, the
selection logic is `eligibility.is_due_for_week4_reminder()` and a cron
calling `POST /admin/reminders/send` would be enough.

## The form itself

Each lettered section (A, B, C...) is its own step. The stepper is plain
JS with no build step: the form is still one `<form>` that posts
everything at once, so if the script fails to load the page degrades to a
long scrolling form and nothing is lost. It also autosaves to
`localStorage` per stage+batch, and clears that on submit.

Note the `novalidate` on each form -- it's load-bearing. Hidden steps
contain `required` fields, and native validation refuses to focus a
hidden invalid control, which would silently block submission. Validation
is done per-step in `stepper.js` instead, and every field is re-checked
server-side.

Visually the three surveys share one system: warm newsprint, halftone
grain, and a different ink colour per section (violet, coral, aqua,
tangerine) so moving through the form visibly changes the page. Colours
are CSS custom properties keyed on `data-accent`, so re-skinning is a
handful of lines at the top of `style.css`.

## When a submit goes wrong

Submitting is the one moment a student cannot repeat -- Pre is write-once,
and the Post Survey 1 window closes the same night -- so nothing after the
answers are stored is allowed to take that page away from them.

**The result email is sent after the page, not before it.** Building the
PNG charts and talking to the SMTP server used to happen inline, on the
event loop, while the student waited: a mail server that was slow, rate-
limiting a cohort of submissions, or simply down turned a *saved*
submission into an error page. `email_utils.queue_send` now hands that
work to a small background thread pool and returns immediately, logging
`[email] <stage> failed:` with a traceback if the mail never leaves. The
student's answers and their on-screen result are unaffected either way.
`SMTP_TIMEOUT` (default 20s) caps how long one send can hang.

The pool is deliberately small -- `EMAIL_WORKERS`, default 4 per worker
process. Rendering the charts is CPU work holding the GIL, so an unbounded
fan-out would have hundreds of threads drawing PNGs while other students
are still waiting for their result page. A handful of mail threads means a
backlog drains a little later, which nobody notices, rather than the site
slowing down, which everybody does.

Because those charts are now drawn on background threads, `charts_email.py`
uses matplotlib's `Figure` API directly rather than `pyplot` -- pyplot's
global figure registry is not thread-safe, and two students submitting at
the same moment would otherwise draw into each other's chart.

**The reminder run is backgrounded too, down one SMTP connection.**
`POST /admin/reminders/send` used to open a fresh authenticated connection
per student and send the lot inline; at 500 students that is both what tips
a provider into rate-limiting and a request that runs far past gunicorn's
timeout, leaving the admin on a dead page with no idea how far it got. The
page now comes straight back saying how many are going out, and
`email_utils.send_many` works through them on one connection, recording
each send against that student's Pre document *as it lands* -- so
refreshing the page is an accurate picture of where the run has reached. A
refused address is logged and skipped; a connection the provider drops
mid-run is re-established once.

**Errors are pages, not JSON.** A blank answer that slips past the
per-step validation used to come back as a raw `{"detail": "Missing
required field: Scenario B3"}`. Two handlers in `main.py` now render
`error.html` instead -- naming the question that was left blank, with a
link back to the form -- and a catch-all handler turns any unexpected
crash into a readable page while logging the traceback to
`/var/log/job-intelligence-survey/error.log`. Anything under `/api/`,
along with the admin's 401 challenge, keeps its original machine-readable
behaviour, so `stepper.js` and the browser auth prompt are untouched.

## Running this for 500+ students

The load that matters is not the total, it's that a cohort submits inside
the same ten minutes at the end of a session.

**Sizing.** `--workers` in the systemd unit should be `(2 x nproc) + 1`;
check `nproc` on the VPS rather than trusting the 5 in the committed file.
Nothing slow happens inside a request any more -- mail and chart rendering
are on the background pool -- so each worker turns submissions over
quickly.

**The real ceiling is your mail provider, not this app.** 500 students x
three surveys is 1500+ emails, plus reminders. A Gmail app password caps
out around 500 recipients/day (2000 on Workspace), which a single cohort
will exhaust: the app will keep serving result pages perfectly while every
send is refused and logged. If you are running at this size, use a
transactional provider (SES, SendGrid, Postmark) and check the day's
`[email]` lines in the service log after a session:

```bash
journalctl -u job-intelligence-survey --since today | grep '^\[email\]'
```

If a provider objects to the number of simultaneous connections rather than
the daily total, lower `EMAIL_WORKERS` to 2.

**What is not a bottleneck.** Mongo holds one document per student per
stage -- 500 students is 1500 documents, and the admin analysis pages read
the whole batch into memory to compute their charts, which at that size is
milliseconds. The unique index on (email, stage, batch) means a double-tap
on Submit overwrites rather than duplicating, so a cohort refreshing
impatiently cannot inflate the numbers.

## What's server-computed vs. what isn't

Every score shown to a student or to admin — Job Intelligence total and
per-dimension, Job Search total, quadrant, control-item drift — is
computed once in `app/scoring.py` at submission time and stored in Mongo.
Nothing is recomputed client-side. If you ever need to re-score historical
responses after a scoring-key change, the raw answers are stored
unmodified in `raw_answers` on every document, so you can write a one-off
migration script against `responses_collection()` without needing new
survey submissions.

## Known gaps, honestly

- No rate limiting or CAPTCHA on the survey POST endpoints — fine for an
  internal-domain workshop tool, not fine if the links ever leak publicly.
- `/status` takes an email in the query string with no verification. It
  only ever reveals which of three surveys that address has filled in —
  no answers, no scores — but it is an unauthenticated existence check,
  so treat the deployment as internal.
- Gating is measured in UTC and displayed in UTC. For a Bengaluru cohort
  the Post Survey 1 window is generous enough (14h) that IST vs UTC doesn't
  bite, but if you shorten it, account for the 5h30m offset.
- Admin auth is HTTP Basic over the single `ADMIN_USER`/`ADMIN_PASSWORD`
  pair — adequate for one or two people checking the dashboard, not a
  real multi-admin auth system.
- The comparison-group (non-attendee) data flagged as important in the
  instrument's Known Limitations isn't handled specially here — if you
  collect it, it'll need its own `batch` value (e.g.
  `2026-final-year-control`) so it doesn't get folded into the main
  cohort's quadrant/dimension aggregates by accident.

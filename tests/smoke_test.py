"""
Dev-only smoke test. Uses mongomock so it needs no real MongoDB instance.
Not part of the shipped app -- mongomock is a test-time dependency only.
Run: python3 tests/smoke_test.py   (from the project root)

Covers the full student arc AND the stage gating, including the case that
matters most: week-4 must be refused before day 30 and accepted after it.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock
from app import db as db_module
db_module._client = mongomock.MongoClient()

os.environ.setdefault("EMAIL_ENABLED", "false")  # don't try to actually send mail

from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app import eligibility

client = TestClient(app)
BATCH = "smoke-test-batch"
EMAIL = "test.student@example.com"
failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def backdate_pre(days):
    """Pretend this student's Pre was submitted `days` days ago."""
    db_module.responses_collection().update_one(
        {"email_norm": EMAIL, "stage": "pre", "batch": BATCH},
        {"$set": {"submitted_at": datetime.now(timezone.utc) - timedelta(days=days)}},
    )


# ---- GET pages render ----
for path in ["/", "/status", "/survey/pre", "/survey/post-sameday", "/survey/post-week4"]:
    r = client.get(f"{path}?batch={BATCH}")
    check(f"GET {path} -> 200", r.status_code == 200)

r = client.get(f"/survey/pre?batch={BATCH}")
check("Pre form is a stepper", "data-stepper" in r.text and 'data-stage="pre"' in r.text)
check("Pre form has 5 sections", r.text.count('class="step ') == 5)
check("Pre form skips native validation (hidden steps)", "novalidate" in r.text)

# ---- PRE ----
PRE_PAYLOAD = {
    "name": "Test Student", "email": EMAIL, "batch": BATCH,
    "a1": "16-30", "a2": "1-2", "a3": "1", "a4": "under_10", "a5": "4",
    "b1": "a", "b2": "a", "b3": "a", "b4": "a", "b5": "a",
    "c1": "4", "c2": "3", "c3": "3",
    "d1": "Not enough companies visit our campus.",
}
r = client.post(f"/survey/pre?batch={BATCH}", data=PRE_PAYLOAD)
check("POST /survey/pre -> 200", r.status_code == 200)
check("Pre result names a quadrant", "Volume Applicant" in r.text)

doc = db_module.get_response(EMAIL, "pre", BATCH)
check("Pre doc stored", doc is not None)
check("Pre job_search scored", doc and doc["scores"]["job_search"]["total"] is not None)
check("Pre job_intelligence scored", doc and doc["scores"]["job_intelligence"]["total"] is not None)

# The whole point of the change: same-day is visible the moment Pre lands.
check("Pre result offers the same-day survey now", "/survey/post-sameday" in r.text)
check("Pre result shows week-4 as locked", "Unlocks in" in r.text)

# ---- api/check reflects the gate ----
j = client.get(f"/api/check?stage=post_sameday&batch={BATCH}&email={EMAIL}").json()
check("api/check: same-day open after Pre", j["ok"] and j["state"] == "open")
j = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL}").json()
check("api/check: week-4 locked before day 30", (not j["ok"]) and j["state"] == "locked")
j = client.get(f"/api/check?stage=post_sameday&batch={BATCH}&email=nobody@example.com").json()
check("api/check: same-day locked with no Pre", (not j["ok"]) and j["state"] == "locked")

# ---- SAME-DAY ----
SAMEDAY_PAYLOAD = {
    "name": "Test Student", "email": EMAIL, "batch": BATCH,
    "b1": "d", "b2": "d", "b3": "c", "b4": "d", "b5": "d",
    "c1": "4", "c2": "3", "c3": "3",
    "d1": "2", "d2": "4",
    "e1": ["tasks", "combo"],
    "e2": "Sports analytics overlap between data and design.",
    "f1": "I don't know how to combine two skills into one pitch.",
    "f2": "market_gaps", "f3": "More time on the group activity.",
}
r = client.post(f"/survey/post-sameday?batch={BATCH}", data=SAMEDAY_PAYLOAD)
check("POST same-day -> 200", r.status_code == 200)
sd = db_module.get_response(EMAIL, "post_sameday", BATCH)
check("Same-day matched to Pre", sd and sd["scores"]["matched_pre"] is True)
check("Same-day delta computed", sd and sd["scores"]["ji_delta_vs_pre"] > 0)

# ---- WEEK 4: blocked before 30 days ----
WEEK4_PAYLOAD = {
    "name": "Test Student", "email": EMAIL, "batch": BATCH,
    "a1": "1-5", "a2": "9-10", "a3": "2-3", "a4": "more_than_60", "a5": "1",
    "b1": "d", "b2": "d", "b3": "d", "b4": "d", "b5": "d",
    "c1": "4", "c2": "3", "c3": "3",
    "d1": "yes", "d2": "Product designer; competitor research.",
    "d3": "significantly", "d4": "Rebuilt it around problems I solve.",
    "e1": "2-3", "e2": "1", "e3": "Getting past the first screening.",
}
r = client.post(f"/survey/post-week4?batch={BATCH}", data=WEEK4_PAYLOAD)
check("Week-4 POST refused before day 30", "Not open" in r.text or "Unlocks in" in r.text)
check("Week-4 not written while locked", db_module.get_response(EMAIL, "post_week4", BATCH) is None)

r = client.get(f"/survey/post-week4?batch={BATCH}&email={EMAIL}")
check("Week-4 GET shows the locked page", "Unlocks in" in r.text and "data-stepper" not in r.text)

# ---- reminders: nobody due yet ----
auth = ("admin", "change-me")
r = client.get(f"/admin/reminders?batch={BATCH}", auth=auth)
check("Admin reminders -> 200", r.status_code == 200)
check("Nobody due before day 30", "Nobody is due" in r.text)

# ---- now roll the clock forward past the 30 days ----
backdate_pre(31)

j = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL}").json()
check("api/check: week-4 open after day 30", j["ok"] and j["state"] == "open")

r = client.get(f"/admin/reminders?batch={BATCH}", auth=auth)
check("Student appears as due after day 30", EMAIL in r.text)

r = client.post("/admin/reminders/send", data={"batch": BATCH}, auth=auth, follow_redirects=False)
check("Bulk reminder send redirects", r.status_code == 303)
# EMAIL_ENABLED=false means nothing actually leaves, so nothing is logged either
check("Nothing logged when mail is off",
      (db_module.get_response(EMAIL, "pre", BATCH) or {}).get("week4_reminder_count") is None)

# signed link should validate
tok = eligibility.make_token(EMAIL, BATCH)
check("Signed week-4 token validates", eligibility.check_token(tok, EMAIL, BATCH))
check("Tampered token rejected", not eligibility.check_token("deadbeef", EMAIL, BATCH))

r = client.get(f"/survey/post-week4?batch={BATCH}&email={EMAIL}&t={tok}")
check("Signed link prefills the name", 'value="Test Student"' in r.text)

# ---- WEEK 4: accepted now ----
r = client.post(f"/survey/post-week4?batch={BATCH}", data=WEEK4_PAYLOAD)
check("POST week-4 -> 200 after unlock", r.status_code == 200)
w4 = db_module.get_response(EMAIL, "post_week4", BATCH)
check("Week-4 doc stored", w4 is not None)
check("Week-4 JI rose vs Pre", w4 and w4["scores"]["ji_delta_vs_pre"] > 0)
check("Week-4 untargeted search fell vs Pre", w4 and w4["scores"]["job_search_delta_vs_pre"] < 0)

# ---- resubmission overwrites rather than duplicating ----
client.post(f"/survey/pre?batch={BATCH}", data=PRE_PAYLOAD)
check("Resubmitting Pre does not duplicate",
      len(db_module.all_responses(BATCH, "pre")) == 1)

# ---- status page ----
r = client.get(f"/status?batch={BATCH}&email={EMAIL}")
check("Status page -> 200", r.status_code == 200)
check("Status page shows three stages", r.text.count('class="stage ') == 3)
check("Status page shows submitted state", "Submitted" in r.text)

# ---- admin ----
r = client.get(f"/admin?batch={BATCH}", auth=auth)
check("Admin dashboard -> 200", r.status_code == 200)
r = client.get(f"/admin?batch={BATCH}")
check("Admin requires auth", r.status_code == 401)
r = client.get(f"/admin/export.csv?batch={BATCH}", auth=auth)
check("CSV export -> 200", r.status_code == 200 and "job_intelligence" in r.text)

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All smoke checks passed.")

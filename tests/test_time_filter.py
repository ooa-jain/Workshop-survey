"""
Filtering an analysis by the time of day the surveys were filled.

The case this exists for: a cohort sat the workshop in the morning, and a
few dozen students filled both Pre and Post over the afternoon. Averaging
them together hides the result, so the analysis has to be able to say
"Pre between 9 and 10, Post between 4 and 5" and drop the rest.

Times are stored in UTC and filtered in local time, which is the whole
difficulty: 09:30 in Bangalore is 04:00 the same morning in UTC.

Run: python3 tests/test_time_filter.py   (from the project root)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["EMAIL_ENABLED"] = "false"

import mongomock
from app import db as db_module
db_module._client = mongomock.MongoClient()

from app.config import settings
settings.EMAIL_ENABLED = False
settings.TIMEZONE = "Asia/Kolkata"

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from app.main import app, build_outcome
from app import localtime

client = TestClient(app)
auth = (settings.ADMIN_USER, settings.ADMIN_PASSWORD)
BATCH = "time-filter-test"
IST = ZoneInfo("Asia/Kolkata")
failures = []


def check(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def ist(h, m=0):
    """A moment on workshop day, written in local time, stored as UTC."""
    return datetime(2026, 8, 18, h, m, tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)


def submit(name, email, stage, when, ji_answer="a"):
    """Write a response straight to the collection so its timestamp is ours."""
    from app import scoring
    answers = {f"b{i}": ji_answer for i in range(1, 6)}
    ji = scoring.score_job_intelligence([ji_answer] * 5)
    scores = {"job_intelligence": ji, "control_mean": 3.0}
    if stage == "pre":
        js = scoring.score_job_search("1-2", "1", "under_10", "4")
        scores["job_search"] = js
        scores["quadrant"] = scoring.quadrant(js["total"], ji["total"])
    db_module.responses_collection().insert_one({
        "email": email, "email_norm": email.lower(), "name": name,
        "stage": stage, "batch": BATCH, "raw_answers": answers,
        "scores": scores, "submitted_at": when, "submission_count": 1,
    })


# Morning cohort: Pre ~9:30, Post ~16:30. Afternoon stragglers: both after lunch.
for i in range(10):
    submit(f"Morning {i}", f"morning{i}@x.com", "pre", ist(9, 20 + i))
    submit(f"Morning {i}", f"morning{i}@x.com", "post_sameday", ist(16, 10 + i), ji_answer="d")
for i in range(4):
    submit(f"Afternoon {i}", f"afternoon{i}@x.com", "pre", ist(14, 5 + i))
    submit(f"Afternoon {i}", f"afternoon{i}@x.com", "post_sameday", ist(15, 5 + i), ji_answer="d")

# ---- the stored hour is not the local hour ------------------------------
print("\n-- Stored in UTC, read in local time --")
pre_doc = db_module.get_response("morning0@x.com", "pre", BATCH)
check("A 09:20 local Pre is stored as 03:50 UTC",
      pre_doc["submitted_at"].strftime("%H:%M") == "03:50")
check("...and reads back as 09:20", localtime.fmt(pre_doc["submitted_at"], "%H:%M") == "09:20")
check("...on the local calendar day", db_module.day_of(pre_doc) == "2026-08-18")

# ---- no filter: everybody ------------------------------------------------
print("\n-- No filter --")
o = build_outcome(BATCH)
check("All 14 matched pairs counted", o["n"] == 14)
check("No window reported as active", o["window_active"] is False)

# ---- the filter Harsha asked for ----------------------------------------
print("\n-- Pre 9-10, Post 16-17 --")
w = db_module.time_window("09:00", "10:00", "16:00", "17:00")
o = build_outcome(BATCH, window=w)
check("Only the morning cohort counted", o["n"] == 10)
check("The afternoon pairs are excluded", o["n_excluded"] == 4)
check("Unfiltered total still reported", o["n_unfiltered"] == 14)
check("Window reported as active", o["window_active"] is True)
check("Window reads back in local time",
      o["window_pre_label"] == "09:00–10:00" and o["window_post_label"] == "16:00–17:00")
check("Nobody from the afternoon is in the rows",
      not any("Afternoon" in r["name"] for r in o["rows"]))

# ---- one bound only ------------------------------------------------------
print("\n-- Half-open windows --")
o = build_outcome(BATCH, window=db_module.time_window(pre_to="10:00"))
check("Pre before 10:00 keeps the morning group only", o["n"] == 10)
o = build_outcome(BATCH, window=db_module.time_window(post_from="16:00"))
check("Post after 16:00 keeps the morning group only", o["n"] == 10)
o = build_outcome(BATCH, window=db_module.time_window(pre_from="13:00", pre_to="15:00"))
check("An afternoon Pre window keeps only those four", o["n"] == 4)

# ---- a student whose Pre qualifies but Post does not --------------------
print("\n-- Both surveys must qualify --")
submit("Late Post", "latepost@x.com", "pre", ist(9, 45))
submit("Late Post", "latepost@x.com", "post_sameday", ist(21, 0), ji_answer="d")
o = build_outcome(BATCH, window=db_module.time_window("09:00", "10:00", "16:00", "17:00"))
check("A qualifying Pre with a late Post is still excluded", o["n"] == 10)
o = build_outcome(BATCH, window=db_module.time_window("09:00", "10:00"))
check("...but counts when only the Pre is bounded", o["n"] == 11)

# ---- through the admin page and a share link ----------------------------
print("\n-- Through the UI --")
r = client.get(f"/admin/outcome?batch={BATCH}&pre_from=09:00&pre_to=10:00"
               f"&post_from=16:00&post_to=17:00", auth=auth)
check("Outcome tab -> 200", r.status_code == 200)
# By now the suite has added 15 matched pairs: 10 morning, 4 afternoon, and
# the one whose Post came in at 21:00.
check("Outcome tab says what it excluded", "5 of 15 matched students excluded" in r.text)
check("Outcome tab shows the window", "09:00–10:00" in r.text)

r = client.post("/admin/share/create", data={
    "batch": BATCH, "title": "Morning session", "day": "",
    "pre_from": "09:00", "pre_to": "10:00", "post_from": "16:00", "post_to": "17:00",
    "show_names": "on"}, auth=auth, follow_redirects=False)
check("Share created -> 303", r.status_code == 303)
token = r.headers["location"].split("new=")[1]

r = client.get(f"/s/{token}")
check("Shared page -> 200", r.status_code == 200)
check("Shared page states its time filter", "Pre 09:00–10:00" in r.text)
check("Shared page counts only the morning cohort", ">10<" in r.text)
check("Shared page names nobody from the afternoon", "Afternoon" not in r.text)

r = client.get(f"/s/{token}/students.xlsx")
check("Shared spreadsheet still downloads", r.status_code == 200)

# A link made before the filter existed has no bounds stored, and must behave
# exactly as it always did.
r = client.post("/admin/share/create",
                data={"batch": BATCH, "title": "Everyone", "day": "", "show_names": "on"},
                auth=auth, follow_redirects=False)
old_token = r.headers["location"].split("new=")[1]
r = client.get(f"/s/{old_token}")
check("A share with no window counts everybody", ">15<" in r.text)
check("...and claims no time filter", "who filled" not in r.text)

print("\n" + ("All time-filter checks passed." if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)

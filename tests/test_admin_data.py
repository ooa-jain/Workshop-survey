"""
The admin data tabs: reading back exactly what a student filled, and
spotting the same person entered twice.

Run: python3 tests/test_admin_data.py   (from the project root)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["EMAIL_ENABLED"] = "false"

import mongomock
from app import db as db_module
db_module._client = mongomock.MongoClient()

from app.config import settings
settings.EMAIL_ENABLED = False

from fastapi.testclient import TestClient
from app.main import app
from app import questions

client = TestClient(app)
auth = (settings.ADMIN_USER, settings.ADMIN_PASSWORD)
BATCH = "data-tab-test"
failures = []


def check(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def pre_payload(name, email, **over):
    d = {
        "name": name, "email": email, "batch": BATCH, "password": "pw-" + email,
        "a1": "16-30", "a2": "1-2", "a3": "1", "a4": "under_10", "a5": "4",
        "b1": "a", "b2": "a", "b3": "a", "b4": "a", "b5": "a",
        "c1": "4", "c2": "3", "c3": "3",
        "d1": "Not enough companies visit our campus.",
    }
    d.update(over)
    return d


def sameday_payload(name, email, **over):
    d = {
        "name": name, "email": email, "batch": BATCH,
        "b1": "d", "b2": "d", "b3": "d", "b4": "d", "b5": "d",
        "c1": "4", "c2": "3", "c3": "3",
        "d1": "2", "d2": "5",
        "e1": ["tasks", "cv"],
        "e2": "Nobody here does sports analytics.",
        "f1": "Getting a first interview.",
        "f2": ["market_gaps"],
        "f3": "More time on the AI section.",
    }
    d.update(over)
    return d


client.post(f"/survey/pre?batch={BATCH}", data=pre_payload("Anita Rao", "anita@example.com"))
client.post(f"/survey/post-sameday?batch={BATCH}", data=sameday_payload("Anita Rao", "anita@example.com"))
client.post(f"/survey/pre?batch={BATCH}", data=pre_payload("Vikram Shah", "vikram@example.com"))

# ---- the all-data browser ------------------------------------------------
print("\n-- All data --")
r = client.get(f"/admin/data?batch={BATCH}", auth=auth)
check("All data -> 200", r.status_code == 200)
check("Lists every submission", r.text.count("Open &rarr;") == 3)
check("Shows both students", "anita@example.com" in r.text and "vikram@example.com" in r.text)
check("All data requires auth", client.get(f"/admin/data?batch={BATCH}").status_code == 401)

r = client.get(f"/admin/data?batch={BATCH}&q=vikram", auth=auth)
check("Search narrows to one student", "vikram@example.com" in r.text and "anita@example.com" not in r.text)

r = client.get(f"/admin/data?batch={BATCH}&stage=post_sameday", auth=auth)
check("Stage filter narrows to one survey", r.text.count("Open &rarr;") == 1)

# ---- one submission in full ---------------------------------------------
print("\n-- Every answer --")
r = client.get(f"/admin/data/response?batch={BATCH}&email=anita@example.com&stage=pre", auth=auth)
check("Response view -> 200", r.status_code == 200)
check("Shows the question text, not just the code",
      "biggest thing standing between you and the role you want" in r.text)
check("Shows what they typed", "Not enough companies visit our campus." in r.text)
check("Shows the option label, not the stored code", "16–30" in r.text and "Under 10 minutes" in r.text)
check("Covers every question on the stage",
      all(q["num"] in r.text for q in questions.QUESTIONS["pre"]))

r = client.get(f"/admin/data/response?batch={BATCH}&email=anita@example.com&stage=post_sameday", auth=auth)
check("Multi-select answers are spelled out",
      "Rewrite my CV around problems I can solve" in r.text)
check("Likert answers read as a scale", "5 of 5" in r.text)
check("Unknown student -> 404",
      client.get(f"/admin/data/response?batch={BATCH}&email=nobody@example.com&stage=pre",
                 auth=auth).status_code == 404)

# ---- a group row opens that student's answers ---------------------------
print("\n-- From the Groups tab --")
r = client.get(f"/admin/groups?batch={BATCH}", auth=auth)
check("Group rows link to the answers",
      "/admin/data/response?batch=" in r.text and "Answers &rarr;" in r.text)

# ---- refilling the same survey ------------------------------------------
print("\n-- Refilled the same survey --")
client.post(f"/survey/post-sameday?batch={BATCH}",
            data=sameday_payload("Anita Rao", "anita@example.com", f1="Actually, my CV."))
doc = db_module.get_response("anita@example.com", "post_sameday", BATCH)
check("The refill overwrote the answers", doc["raw_answers"]["f1"] == "Actually, my CV.")
check("...and the refill was counted", doc.get("submission_count") == 2)
check("...with the first sitting still on record", doc.get("first_submitted_at") is not None)

r = client.get(f"/admin/duplicates?batch={BATCH}", auth=auth)
check("Duplicates tab -> 200", r.status_code == 200)
check("Refill is listed as filled twice", "Anita Rao" in r.text and ">2<" in r.text)
check("Refill is not called a duplicate entry", "No duplicates found" in r.text)

# ---- the same person under two addresses --------------------------------
print("\n-- Same person, two addresses --")
client.post(f"/survey/pre?batch={BATCH}", data=pre_payload("Anita Rao", "anita.rao@college.edu"))
r = client.get(f"/admin/duplicates?batch={BATCH}", auth=auth)
check("Duplicate pair is found", "No duplicates found" not in r.text)
check("Both addresses shown", "anita@example.com" in r.text and "anita.rao@college.edu" in r.text)
check("The newest is marked as the one to keep", "Most recent" in r.text and "Superseded" in r.text)
check("Duplicates requires auth", client.get(f"/admin/duplicates?batch={BATCH}").status_code == 401)

# The newest entry here is the college address (submitted last).
r = client.post("/admin/duplicates/resolve",
                data={"batch": BATCH, "keep": "anita.rao@college.edu", "drop": "anita@example.com"},
                auth=auth, follow_redirects=False)
check("Resolve -> 303 redirect", r.status_code == 303)
check("Superseded responses deleted",
      db_module.get_response("anita@example.com", "pre", BATCH) is None)
check("Kept entry untouched",
      db_module.get_response("anita.rao@college.edu", "pre", BATCH) is not None)
check("Resolve requires auth",
      client.post("/admin/duplicates/resolve",
                  data={"batch": BATCH, "keep": "a@b.c", "drop": "d@e.f"},
                  follow_redirects=False).status_code == 401)

r = client.get(f"/admin/duplicates?batch={BATCH}", auth=auth)
check("Duplicate is gone once resolved", "No duplicates found" in r.text)

print("\n" + ("All admin-data checks passed." if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)

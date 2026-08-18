"""
What happens when a submit goes wrong.

A survey submission must never be lost or turned into a wall of raw JSON
because the mail server is down or a student left one answer blank -- with a
whole classroom submitting at once, both are routine.

Run: python3 tests/test_submit_errors.py   (from the project root)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock
from app import db as db_module
db_module._client = mongomock.MongoClient()

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

BATCH = "submit-error-test"
failures = []


def check(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


PAYLOAD = {
    "name": "Test Student", "email": "errors@example.com", "batch": BATCH,
    "password": "my-secret-password",
    "a1": "16-30", "a2": "1-2", "a3": "1", "a4": "under_10", "a5": "4",
    "b1": "a", "b2": "a", "b3": "a", "b4": "a", "b5": "a",
    "c1": "4", "c2": "3", "c3": "3",
    "d1": "Not enough companies visit our campus.",
}

client = TestClient(app)

# ---- A dead mail server must not cost the student their submission -------
# The result email is built and sent after the page has gone out, so an
# unreachable SMTP host is logged and nothing more.
settings.EMAIL_ENABLED = True
settings.SMTP_HOST = "smtp.invalid.example"      # never resolves

print("\n-- SMTP unreachable --")
r = client.post(f"/survey/pre?batch={BATCH}", data=PAYLOAD)
check("Submit still returns the result page", r.status_code == 200)
check("Result page names a quadrant", "Volume Applicant" in r.text)
check("Answers were stored", db_module.get_response(PAYLOAD["email"], "pre", BATCH) is not None)

settings.EMAIL_ENABLED = False

# ---- A blank answer must read as English, not as JSON --------------------
print("\n-- Missing answer --")
incomplete = dict(PAYLOAD, email="incomplete@example.com")
incomplete.pop("b3")
r = client.post(f"/survey/pre?batch={BATCH}", data=incomplete)
check("Missing answer -> 400", r.status_code == 400)
check("Missing answer -> HTML page", r.headers["content-type"].startswith("text/html"))
check("Missing answer names the question", "Scenario B3" in r.text)
check("Missing answer is not raw JSON", not r.text.lstrip().startswith("{"))
check("Missing answer offers a way back", "Back to the form" in r.text)
check("Nothing was stored for an incomplete form",
      db_module.get_response("incomplete@example.com", "pre", BATCH) is None)

# ---- ...but machine callers keep their JSON ------------------------------
print("\n-- Machine callers --")
r = client.get(f"/api/check?stage=nope&batch={BATCH}&email={PAYLOAD['email']}")
check("API errors stay JSON", r.status_code == 400 and r.json()["detail"] == "Unknown stage")
r = client.get("/admin")
check("Admin 401 still prompts for credentials",
      r.status_code == 401 and "WWW-Authenticate" in r.headers)

# ---- An unexpected crash shows a page, not 'Internal Server Error' -------
print("\n-- Unexpected crash --")
lenient = TestClient(app, raise_server_exceptions=False)
real_get_response = db_module.get_response
db_module.get_response = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    r = lenient.post(f"/survey/pre?batch={BATCH}", data=dict(PAYLOAD, email="crash@example.com"))
    check("Crash -> 500", r.status_code == 500)
    check("Crash -> readable page", "Something broke" in r.text)
finally:
    db_module.get_response = real_get_response

print("\n" + ("All submit-error checks passed." if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)

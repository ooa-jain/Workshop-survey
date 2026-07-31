import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock
from app import db as db_module

db_module._client = mongomock.MongoClient()

from app.config import settings
settings.EMAIL_ENABLED = False
settings.SMTP_HOST = ""

from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app import eligibility

client = TestClient(app)
BATCH = "test-batch"
EMAIL1 = "user1@example.com"
EMAIL2 = "user2@example.com"
auth = ("admin", "change-me")


def test_dev_mode_and_individual_grant():
    # 1. Submit pre for two users
    pre_payload_1 = {
        "name": "User One", "email": EMAIL1, "batch": BATCH,
        "a1": "16-30", "a2": "1-2", "a3": "1", "a4": "under_10", "a5": "4",
        "b1": "a", "b2": "a", "b3": "a", "b4": "a", "b5": "a",
        "c1": "4", "c2": "3", "c3": "3", "d1": "None",
    }
    pre_payload_2 = {
        "name": "User Two", "email": EMAIL2, "batch": BATCH,
        "a1": "16-30", "a2": "1-2", "a3": "1", "a4": "under_10", "a5": "4",
        "b1": "a", "b2": "a", "b3": "a", "b4": "a", "b5": "a",
        "c1": "4", "c2": "3", "c3": "3", "d1": "None",
    }
    client.post(f"/survey/pre?batch={BATCH}", data=pre_payload_1)
    client.post(f"/survey/pre?batch={BATCH}", data=pre_payload_2)

    # 2. Verify initially Week-4 is locked for both
    j1 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL1}").json()
    j2 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL2}").json()
    assert j1["state"] == "locked", f"Expected locked, got {j1['state']}"
    assert j2["state"] == "locked", f"Expected locked, got {j2['state']}"
    print("PASS: Initially both users locked for Week 4")

    # 3. Grant individual access to EMAIL1
    r = client.post("/admin/reminders/grant-access", data={"batch": BATCH, "email": EMAIL1, "enabled": "true"}, auth=auth, follow_redirects=False)
    assert r.status_code == 303, f"Expected 303 redirect, got {r.status_code}"

    j1 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL1}").json()
    j2 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL2}").json()
    assert j1["ok"] and j1["state"] == "open", f"Expected EMAIL1 open, got {j1}"
    assert j2["state"] == "locked", f"Expected EMAIL2 locked, got {j2}"
    print("PASS: Individual grant unlocked EMAIL1 while EMAIL2 remains locked")

    # 4. Revoke individual access for EMAIL1
    client.post("/admin/reminders/grant-access", data={"batch": BATCH, "email": EMAIL1, "enabled": "false"}, auth=auth, follow_redirects=False)
    j1 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL1}").json()
    assert j1["state"] == "locked", f"Expected EMAIL1 relocked, got {j1}"
    print("PASS: Revoking individual access relocked EMAIL1")

    # 5. Enable Developer Mode globally
    client.post("/admin/reminders/dev-mode", data={"batch": BATCH, "enabled": "true"}, auth=auth, follow_redirects=False)
    j1 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL1}").json()
    j2 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL2}").json()
    assert j1["ok"] and j1["state"] == "open", f"Dev mode: expected EMAIL1 open, got {j1}"
    assert j2["ok"] and j2["state"] == "open", f"Dev mode: expected EMAIL2 open, got {j2}"
    print("PASS: Developer mode unlocked ALL users at a time for Week 4")

    # 6. Disable Developer Mode globally
    client.post("/admin/reminders/dev-mode", data={"batch": BATCH, "enabled": "false"}, auth=auth, follow_redirects=False)
    j1 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL1}").json()
    j2 = client.get(f"/api/check?stage=post_week4&batch={BATCH}&email={EMAIL2}").json()
    assert j1["state"] == "locked" and j2["state"] == "locked"
    print("PASS: Disabling Developer mode relocked users")

    # 7. Check admin reminders page renders dev mode banner and actions
    r = client.get(f"/admin/reminders?batch={BATCH}", auth=auth)
    assert r.status_code == 200
    assert "Developer Mode" in r.text
    assert "Grant Access" in r.text
    print("PASS: Admin reminders page renders Developer Mode controls and Grant Access buttons")


if __name__ == "__main__":
    test_dev_mode_and_individual_grant()
    print("ALL TESTS PASSED SUCCESSFULLY!")

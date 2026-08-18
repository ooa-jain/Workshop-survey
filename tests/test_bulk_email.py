"""
Mail at cohort scale.

A workshop cohort is 500+ students. Sending that many result emails or
reminders has to stay off the request path, reuse one SMTP connection, and
survive individual bad addresses -- none of which the request/response cycle
gives you for free.

Run: python3 tests/test_bulk_email.py   (from the project root)
"""
import sys, os, smtplib, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import email_utils
from app.config import settings

failures = []


def check(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


class FakeSMTP:
    """Stands in for a real connection, counting what it was asked to do."""
    connections = 0
    sent = []
    fail_for = set()
    disconnect_after = None

    def __init__(self):
        FakeSMTP.connections += 1
        self.count = 0

    def send_message(self, msg):
        to = msg["To"]
        self.count += 1
        if FakeSMTP.disconnect_after is not None and self.count > FakeSMTP.disconnect_after:
            raise smtplib.SMTPServerDisconnected("connection closed by provider")
        if any(bad in to for bad in FakeSMTP.fail_for):
            raise smtplib.SMTPRecipientsRefused({to: (550, b"no such user")})
        FakeSMTP.sent.append(to)

    def quit(self):
        pass


def reset(fail_for=(), disconnect_after=None):
    FakeSMTP.connections = 0
    FakeSMTP.sent = []
    FakeSMTP.fail_for = set(fail_for)
    FakeSMTP.disconnect_after = disconnect_after
    email_utils._connect = lambda: FakeSMTP()


settings.EMAIL_ENABLED = True
settings.SMTP_HOST = "smtp.test.invalid"

COHORT = [(f"student{i:03d}@example.com", f"Student {i}", "Subject", "<p>hi</p>")
          for i in range(500)]

# ---- 500 reminders, one connection ---------------------------------------
print("\n-- A 500-student run --")
reset()
recorded = []
sent, failed = email_utils.send_many("bulk", COHORT, on_sent=recorded.append)
check("All 500 sent", sent == 500 and failed == 0)
check("One SMTP connection for the whole run", FakeSMTP.connections == 1)
check("Every send recorded as it landed", len(recorded) == 500)
check("Recorded in order, no duplicates", recorded == [c[0] for c in COHORT])

# ---- one bad address must not take the run down --------------------------
print("\n-- A bad address mid-run --")
reset(fail_for=["student250@example.com"])
sent, failed = email_utils.send_many("bulk", COHORT)
check("The other 499 still went", sent == 499 and failed == 1)
check("The refused address was skipped",
      "student250@example.com" not in "".join(FakeSMTP.sent))

# ---- a provider dropping the connection mid-run --------------------------
print("\n-- Provider drops the connection --")
reset(disconnect_after=100)
sent, failed = email_utils.send_many("bulk", COHORT)
check("Run reconnects and finishes", sent == 500 and failed == 0)
check("It reconnected rather than giving up", FakeSMTP.connections > 1)

# ---- mail is off the request path ----------------------------------------
print("\n-- Off the request path --")
reset()
done = threading.Event()
slow_started = threading.Event()


def slow_send():
    slow_started.set()
    time.sleep(0.3)
    done.set()


t0 = time.monotonic()
email_utils.queue_send("slow", slow_send)
elapsed = time.monotonic() - t0
check("Queueing returns immediately", elapsed < 0.1)
check("The work does run", done.wait(timeout=5))

# A raising job is logged, not propagated, and does not poison the pool.
email_utils.queue_send("boom", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
after = threading.Event()
email_utils.queue_send("after", after.set)
check("A failing send does not stop later ones", after.wait(timeout=5))

# ---- mail switched off entirely ------------------------------------------
print("\n-- EMAIL_ENABLED=false --")
settings.EMAIL_ENABLED = False
sent, failed = email_utils.send_many("bulk", COHORT[:5])
check("Nothing sent and nothing counted", (sent, failed) == (0, 0))

print("\n" + ("All bulk-email checks passed." if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)

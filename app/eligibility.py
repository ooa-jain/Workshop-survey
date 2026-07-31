"""
Stage gating for the three surveys.

Rules (all per-student, keyed on email + batch):

  PRE          always open. Resubmitting overwrites.
  SAME-DAY     opens the instant that student's PRE lands; closes
               SAMEDAY_WINDOW_HOURS later.
  WEEK-4       stays locked until WEEK4_UNLOCK_DAYS after that student's PRE
               (default 30). Admin sends the reminder mail on/after that day;
               the mail carries a signed link. Stays open WEEK4_OPEN_DAYS.

Nothing here touches the DB -- main.py fetches the docs and passes them in,
so this module stays pure and unit-testable.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from .config import settings

STAGES = ("pre", "post_sameday", "post_week4")

STAGE_META = {
    "pre": {
        "title": "Pre",
        "sub": "Before the workshop starts",
        "path": "/survey/pre",
        "minutes": 7,
        "accent": "violet",
    },
    "post_sameday": {
        "title": "Same day",
        "sub": "End of the workshop",
        "path": "/survey/post-sameday",
        "minutes": 6,
        "accent": "coral",
    },
    "post_week4": {
        "title": "Week 4",
        "sub": "One month later",
        "path": "/survey/post-week4",
        "minutes": 7,
        "accent": "aqua",
    },
}


# ---------------------------------------------------------------------------
# Signed links (used in the week-4 reminder email)
# ---------------------------------------------------------------------------

def make_token(email, batch, stage="post_week4"):
    """Short signed token so a reminder link identifies the student without
    exposing anything guessable. Not a session -- it only pre-fills identity
    and proves the link came from us."""
    msg = f"{email.strip().lower()}|{batch}|{stage}".encode()
    return hmac.new(settings.SESSION_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:24]


def check_token(token, email, batch, stage="post_week4"):
    if not token:
        return False
    return hmac.compare_digest(token, make_token(email, batch, stage))


def week4_link(email, batch):
    from urllib.parse import quote
    tok = make_token(email, batch)
    return (f"{settings.BASE_URL}/survey/post-week4"
            f"?batch={quote(batch)}&email={quote(email)}&t={tok}")


def status_link(email, batch):
    from urllib.parse import quote
    return f"{settings.BASE_URL}/status?batch={quote(batch)}&email={quote(email)}"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def week4_unlock_at(pre_doc):
    pre_at = _aware(pre_doc.get("submitted_at")) if pre_doc else None
    if not pre_at:
        return None
    return pre_at + timedelta(days=settings.WEEK4_UNLOCK_DAYS)


def sameday_closes_at(pre_doc):
    pre_at = _aware(pre_doc.get("submitted_at")) if pre_doc else None
    if not pre_at:
        return None
    return pre_at + timedelta(hours=settings.SAMEDAY_WINDOW_HOURS)


def days_until(dt, now=None):
    """Whole days remaining, rounded up, floored at 0."""
    now = now or datetime.now(timezone.utc)
    if dt is None or dt <= now:
        return 0
    gap = dt - now
    return gap.days + (1 if gap.seconds else 0)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def stage_status(stage, arc, now=None, dev_mode=False):
    """
    stage : 'pre' | 'post_sameday' | 'post_week4'
    arc   : {stage: doc} for one student in one batch (db.get_student_arc)

    Returns {state, headline, detail, unlock_at, done_at}
    state is one of: 'done' | 'open' | 'locked' | 'expired'
    """
    now = now or datetime.now(timezone.utc)
    pre = arc.get("pre")
    doc = arc.get(stage)

    if doc:
        return {
            "state": "done",
            "headline": "Submitted",
            "detail": _aware(doc["submitted_at"]).strftime("Received %d %b %Y, %H:%M UTC"),
            "unlock_at": None,
            "done_at": _aware(doc["submitted_at"]),
        }

    if stage == "pre":
        return {"state": "open", "headline": "Open now",
                "detail": "Start here \u2014 everything else unlocks from this one.",
                "unlock_at": None, "done_at": None}

    if not pre:
        return {"state": "locked", "headline": "Locked",
                "detail": "Fill the Pre survey first \u2014 it's the baseline everything is measured against.",
                "unlock_at": None, "done_at": None}

    if settings.GATING_DISABLED or dev_mode:
        return {"state": "open", "headline": "Open now",
                "detail": "Developer mode enabled \u2014 all stages are unlocked.",
                "unlock_at": None, "done_at": None}

    if stage == "post_sameday":
        closes = sameday_closes_at(pre)
        if now > closes:
            return {"state": "expired", "headline": "Window closed",
                    "detail": ("This one only stays open for "
                               f"{settings.SAMEDAY_WINDOW_HOURS} hours after your Pre survey. "
                               "Skip ahead \u2014 your Week-4 survey still counts."),
                    "unlock_at": None, "done_at": None}
        return {"state": "open", "headline": "Open now",
                "detail": f"Closes {closes.strftime('%d %b, %H:%M UTC')}. Takes about 6 minutes.",
                "unlock_at": None, "done_at": None}

    # post_week4
    if pre.get("manual_week4_access"):
        return {"state": "open", "headline": "Unlocked by Admin",
                "detail": "Admin granted individual access to the Week-4 survey.",
                "unlock_at": None, "done_at": None}

    unlock = week4_unlock_at(pre)
    if now < unlock:
        d = days_until(unlock, now)
        return {"state": "locked", "headline": f"Unlocks in {d} day{'s' if d != 1 else ''}",
                "detail": (f"Opens {unlock.strftime('%d %b %Y')} \u2014 "
                           f"{settings.WEEK4_UNLOCK_DAYS} days after your Pre survey. "
                           "We'll email you a link when it does."),
                "unlock_at": unlock, "done_at": None}
    closes = unlock + timedelta(days=settings.WEEK4_OPEN_DAYS)
    if now > closes:
        return {"state": "expired", "headline": "Window closed",
                "detail": f"This closed on {closes.strftime('%d %b %Y')}. Mail the OoA team if you still want to submit.",
                "unlock_at": unlock, "done_at": None}
    return {"state": "open", "headline": "Open now",
            "detail": f"Open until {closes.strftime('%d %b %Y')}. Takes about 7 minutes.",
            "unlock_at": unlock, "done_at": None}


def full_status(arc, now=None, dev_mode=False):
    """Status of all three stages, in order, ready for the template."""
    out = []
    for stage in STAGES:
        st = stage_status(stage, arc, now=now, dev_mode=dev_mode)
        out.append({"stage": stage, **STAGE_META[stage], **st})
    return out


def is_due_for_week4_reminder(arc, now=None, dev_mode=False):
    """True when: pre is in, week-4 isn't, and (unlocked by dev_mode, manual access, or elapsed 30 days)."""
    now = now or datetime.now(timezone.utc)
    if not arc.get("pre") or arc.get("post_week4"):
        return False
    if dev_mode or arc.get("pre", {}).get("manual_week4_access"):
        return True
    unlock = week4_unlock_at(arc["pre"])
    closes = unlock + timedelta(days=settings.WEEK4_OPEN_DAYS)
    return unlock <= now <= closes


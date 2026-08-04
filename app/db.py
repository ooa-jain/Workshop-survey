from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING

from .config import settings

_client = None


def get_client():
    global _client
    if _client is None:
        _client = MongoClient(settings.MONGO_URI)
    return _client


def get_db():
    return get_client()[settings.MONGO_DB]


def responses_collection():
    return get_db()["responses"]


def ensure_indexes():
    coll = responses_collection()
    # one document per (student, stage, batch) -- resubmission overwrites, not duplicates
    coll.create_index(
        [("email_norm", ASCENDING), ("stage", ASCENDING), ("batch", ASCENDING)],
        unique=True,
        name="uniq_student_stage_batch",
    )
    coll.create_index([("batch", ASCENDING), ("stage", ASCENDING)], name="batch_stage")


def upsert_response(email, name, stage, batch, raw_answers, scores, password_hash=None, password_salt=None):
    coll = responses_collection()
    email_norm = email.strip().lower()
    doc = {
        "email": email.strip(),
        "email_norm": email_norm,
        "name": name.strip(),
        "stage": stage,           # 'pre' | 'post_sameday' | 'post_week4'
        "batch": batch,
        "raw_answers": raw_answers,
        "scores": scores,
        "submitted_at": datetime.now(timezone.utc),
    }
    if stage == "pre" and password_hash and password_salt:
        doc["password_hash"] = password_hash
        doc["password_salt"] = password_salt

    coll.update_one(
        {"email_norm": email_norm, "stage": stage, "batch": batch},
        {"$set": doc},
        upsert=True,
    )
    return doc


def update_student_password(email, batch, password_hash, password_salt):
    coll = responses_collection()
    coll.update_one(
        {"email_norm": email.strip().lower(), "stage": "pre", "batch": batch},
        {"$set": {"password_hash": password_hash, "password_salt": password_salt}}
    )


def get_response(email, stage, batch):
    return responses_collection().find_one(
        {"email_norm": email.strip().lower(), "stage": stage, "batch": batch}
    )


def delete_responses_on_date(batch, date_str):
    """Delete every response in this batch submitted on the given calendar
    day (UTC), date_str formatted 'YYYY-MM-DD'. Used by the admin Groups
    page to clear a whole day's worth of submissions at once. Returns the
    number of documents removed."""
    from datetime import timedelta
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    result = responses_collection().delete_many(
        {"batch": batch, "submitted_at": {"$gte": start, "$lt": end}}
    )
    return result.deleted_count


def delete_student(email, batch):
    """Delete every response for a single student in this batch -- their Pre,
    Same-day and Week-4 documents all at once. Used by the admin Individual
    Analysis page to remove one person's data. Returns the number of documents
    removed."""
    email_norm = email.strip().lower()
    result = responses_collection().delete_many(
        {"email_norm": email_norm, "batch": batch}
    )
    return result.deleted_count


def get_student_arc(email, batch):
    """All stages submitted so far for one student, keyed by stage."""
    email_norm = email.strip().lower()
    docs = responses_collection().find({"email_norm": email_norm, "batch": batch})
    return {d["stage"]: d for d in docs}


def all_responses(batch, stage=None):
    q = {"batch": batch}
    if stage:
        q["stage"] = stage
    return list(responses_collection().find(q))


def matched_sameday(batch):
    """Students with both a 'pre' and a 'post_sameday' submission -- the set
    the admin 'Outcome (first day)' analysis is built from, capturing how the
    cohort moved by the end of the workshop day."""
    pre = {d["email_norm"]: d for d in all_responses(batch, "pre")}
    sameday = {d["email_norm"]: d for d in all_responses(batch, "post_sameday")}
    out = []
    for email_norm, pre_doc in pre.items():
        if email_norm in sameday:
            out.append({
                "email": pre_doc["email"],
                "name": pre_doc["name"],
                "pre": pre_doc,
                "sameday": sameday[email_norm],
            })
    return out


def matched_students(batch):
    """Students with both a 'pre' and a 'post_week4' submission -- the set
    the cohort dashboard's quadrant field and slopegraph are built from."""
    pre = {d["email_norm"]: d for d in all_responses(batch, "pre")}
    week4 = {d["email_norm"]: d for d in all_responses(batch, "post_week4")}
    sameday = {d["email_norm"]: d for d in all_responses(batch, "post_sameday")}
    out = []
    for email_norm, pre_doc in pre.items():
        if email_norm in week4:
            out.append({
                "email": pre_doc["email"],
                "name": pre_doc["name"],
                "pre": pre_doc,
                "sameday": sameday.get(email_norm),
                "week4": week4[email_norm],
            })
    return out


# ---------------------------------------------------------------------------
# Cohort view + week-4 reminder bookkeeping
# ---------------------------------------------------------------------------

def cohort_arcs(batch):
    """
    Every student who has at least a Pre response in this batch, with all
    their stages assembled into one arc. This is what the admin Reminders
    page iterates over -- one row per student, not one row per response.
    """
    by_student = {}
    for d in all_responses(batch):
        s = by_student.setdefault(d["email_norm"], {
            "email": d["email"], "name": d["name"], "arc": {},
        })
        s["arc"][d["stage"]] = d
        if d["stage"] == "pre":            # Pre is the authoritative name/email
            s["email"] = d["email"]
            s["name"] = d["name"]
    return [s for s in by_student.values() if "pre" in s["arc"]]


def log_week4_reminder(email, batch, when=None):
    """Record that a week-4 reminder mail went out, on that student's Pre doc.
    Keeps the count and the last-sent timestamp so the admin page can show
    'reminded 2x, last on 3 Sep' instead of re-mailing people blindly."""
    when = when or datetime.now(timezone.utc)
    responses_collection().update_one(
        {"email_norm": email.strip().lower(), "stage": "pre", "batch": batch},
        {"$set": {"week4_reminder_last": when}, "$inc": {"week4_reminder_count": 1}},
    )
    return when


def settings_collection():
    return get_db()["app_settings"]


def get_dev_mode() -> bool:
    doc = settings_collection().find_one({"key": "dev_mode"})
    if doc is not None:
        return bool(doc.get("value", False))
    return settings.GATING_DISABLED


def set_dev_mode(enabled: bool) -> bool:
    settings_collection().update_one(
        {"key": "dev_mode"},
        {"$set": {"value": enabled, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return enabled


def set_manual_week4_access(email: str, batch: str, enabled: bool) -> bool:
    email_norm = email.strip().lower()
    responses_collection().update_one(
        {"email_norm": email_norm, "stage": "pre", "batch": batch},
        {"$set": {"manual_week4_access": enabled, "manual_week4_access_at": datetime.now(timezone.utc)}},
    )
    return enabled


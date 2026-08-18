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
    shares_collection().create_index([("token", ASCENDING)], unique=True, name="uniq_share_token")


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

    # A resubmission overwrites, but the fact that it happened is kept:
    # submission_count is what the admin Duplicates tab reads to show that a
    # student filled the same survey more than once. first_submitted_at is
    # set once and never moved, so the original sitting is still visible
    # after an overwrite.
    coll.update_one(
        {"email_norm": email_norm, "stage": stage, "batch": batch},
        {"$set": doc,
         "$inc": {"submission_count": 1},
         "$setOnInsert": {"first_submitted_at": doc["submitted_at"]}},
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
    Post Survey 1 and Post Survey 2 documents all at once. Used by the admin Individual
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


def day_of(doc):
    """The calendar day (UTC, 'YYYY-MM-DD') a response was submitted on.
    A workshop group is identified by the day its Pre surveys were filled."""
    return doc["submitted_at"].strftime("%Y-%m-%d")


def pre_groups(batch):
    """Every day on which Pre surveys were submitted in this batch, newest
    first, with a headcount. These are the workshop groups an admin can pick
    from when sharing an analysis -- a group is everyone who filled their Pre
    on that day, which is the cohort that sat the workshop together."""
    counts = {}
    for d in all_responses(batch, "pre"):
        counts[day_of(d)] = counts.get(day_of(d), 0) + 1
    return [
        {
            "date": day,
            "label": datetime.strptime(day, "%Y-%m-%d").strftime("%d %b %Y"),
            "n_pre": counts[day],
        }
        for day in sorted(counts, reverse=True)
    ]


def matched_sameday(batch, day=None):
    """Students with both a 'pre' and a 'post_sameday' submission -- the set
    the admin 'Outcome (first day)' analysis is built from, capturing how the
    cohort moved by the end of the workshop day.

    day: optional 'YYYY-MM-DD'. When given, only students whose Pre was
    submitted on that day are included, so one workshop group can be analysed
    (and shared) on its own."""
    pre = {d["email_norm"]: d for d in all_responses(batch, "pre")
           if day is None or day_of(d) == day}
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
# Cohort view + Post Survey 2 reminder bookkeeping
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
    """Record that a Post Survey 2 reminder mail went out, on that student's Pre doc.
    Keeps the count and the last-sent timestamp so the admin page can show
    'reminded 2x, last on 3 Sep' instead of re-mailing people blindly."""
    when = when or datetime.now(timezone.utc)
    responses_collection().update_one(
        {"email_norm": email.strip().lower(), "stage": "pre", "batch": batch},
        {"$set": {"week4_reminder_last": when}, "$inc": {"week4_reminder_count": 1}},
    )
    return when


def stage_count(batch, stage, day=None):
    """How many responses of one stage exist, optionally narrowed to a single
    workshop group. Membership of a group is decided by the Pre response, so a
    Post Survey 1 count for a group means 'people from that group who also filled
    the Post Survey 1', whenever they filled it."""
    if day is None:
        return len(all_responses(batch, stage))
    if stage == "pre":
        return sum(1 for d in all_responses(batch, "pre") if day_of(d) == day)
    group = {d["email_norm"] for d in all_responses(batch, "pre") if day_of(d) == day}
    return sum(1 for d in all_responses(batch, stage) if d["email_norm"] in group)


# ---------------------------------------------------------------------------
# Shared analyses -- read-only public links to a group's first-day results
# ---------------------------------------------------------------------------

def shares_collection():
    return get_db()["shared_analyses"]


def create_share(token, batch, day, title, note="", show_names=True):
    """Store one shareable analysis. day=None shares the whole batch; a
    'YYYY-MM-DD' day shares that one workshop group."""
    doc = {
        "token": token,
        "batch": batch,
        "day": day,
        "title": title.strip(),
        "note": (note or "").strip(),
        "show_names": bool(show_names),
        "created_at": datetime.now(timezone.utc),
        "views": 0,
    }
    shares_collection().insert_one(doc)
    return doc


def get_share(token):
    return shares_collection().find_one({"token": token})


def list_shares(batch):
    return list(shares_collection().find({"batch": batch}).sort("created_at", -1))


def delete_share(token):
    """Revoke a link. The analysis itself is untouched -- only the public
    door to it closes, and the URL stops resolving immediately."""
    return shares_collection().delete_one({"token": token}).deleted_count


def record_share_view(token):
    shares_collection().update_one(
        {"token": token},
        {"$inc": {"views": 1}, "$set": {"last_viewed_at": datetime.now(timezone.utc)}},
    )


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



def _name_key(name):
    """A name reduced to what two records would share if they are the same
    person typed twice: case, punctuation and extra spaces all removed."""
    return " ".join("".join(c for c in (name or "").lower() if c.isalnum() or c.isspace()).split())


def _email_key(email):
    """The identity part of an address -- dots and +tags dropped, so
    first.last@x and firstlast+jobs@x collapse together the way the mail
    provider itself treats them."""
    email = (email or "").strip().lower()
    local, _, domain = email.partition("@")
    local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"


def duplicate_students(batch):
    """
    Students who look like they filled the surveys twice under two different
    email addresses.

    The unique index means one document per (student, stage, batch), so the
    same address filling twice overwrites rather than duplicating -- that
    case shows up as submission_count instead. What this finds is the other
    case: one person entering under two addresses, which no index can catch.

    Two records are treated as the same person when they share a normalised
    name, or an email that differs only by dots or a +tag. Each returned
    cluster is ordered newest first, so entries[0] is the one to keep and
    everything after it is superseded.
    """
    by_student = {}
    for d in all_responses(batch):
        s = by_student.setdefault(d["email_norm"], {
            "email": d["email"], "email_norm": d["email_norm"], "name": d["name"],
            "stages": {}, "last_at": d["submitted_at"], "resubmissions": 0,
        })
        s["stages"][d["stage"]] = d
        s["last_at"] = max(s["last_at"], d["submitted_at"])
        s["resubmissions"] += max(0, (d.get("submission_count") or 1) - 1)
        if d["stage"] == "pre":
            s["email"], s["name"] = d["email"], d["name"]

    clusters = {}
    for s in by_student.values():
        clusters.setdefault(("name", _name_key(s["name"])), []).append(s)
        clusters.setdefault(("email", _email_key(s["email"])), []).append(s)

    seen, out = set(), []
    for (kind, key), members in clusters.items():
        if len(members) < 2:
            continue
        signature = tuple(sorted(m["email_norm"] for m in members))
        if signature in seen:
            continue
        seen.add(signature)
        members.sort(key=lambda m: m["last_at"], reverse=True)
        out.append({"reason": kind, "key": key, "entries": members})

    out.sort(key=lambda c: c["entries"][0]["last_at"], reverse=True)
    return out


def resubmitted_students(batch):
    """Students who filled the same survey more than once under the same
    address. The later answers overwrote the earlier ones -- which is the
    intended behaviour -- so this is a record of it having happened, not
    something to clean up."""
    out = []
    for d in all_responses(batch):
        count = d.get("submission_count") or 1
        if count > 1:
            out.append({
                "email": d["email"], "name": d["name"], "stage": d["stage"],
                "count": count,
                "first_at": d.get("first_submitted_at"),
                "last_at": d["submitted_at"],
            })
    out.sort(key=lambda r: r["last_at"], reverse=True)
    return out

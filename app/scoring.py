"""
Scoring logic for the Job Search -> Job Intelligence instrument.

Kept dependency-free and pure (no DB, no I/O) so it can be unit tested
in isolation. Every mapping here corresponds directly to the
"SCORING KEY" section of job-intelligence-survey-instrument.md --
if that document changes, this file is the only place to update.
"""

from statistics import mean

# ---------------------------------------------------------------------------
# Job Intelligence (targeted) -- five scenario items, each scored a=0..d=3
# ---------------------------------------------------------------------------

JI_OPTION_SCORE = {"a": 0, "b": 1, "c": 2, "d": 3}

JI_DIMENSIONS = [
    {"key": "unit_of_work",       "left": "Role-based",        "right": "Task-based",
     "desc": "Unit of work"},
    {"key": "value_source",       "left": "Knowledge",         "right": "Judgment",
     "desc": "Where value sits"},
    {"key": "optimisation_target","left": "Scarcity",          "right": "Coordination & risk",
     "desc": "What you optimise for"},
    {"key": "opportunity_shape",  "left": "Single skill",      "right": "Skill combination",
     "desc": "How opportunity is found"},
    {"key": "ai_role",            "left": "Shortcut",          "right": "Thinking partner",
     "desc": "Role of AI"},
]


def score_job_intelligence(answers):
    """
    answers: list of 5 strings, each one of 'a'|'b'|'c'|'d', in item order
             (matches B1..B5 on any form -- Form A and Form B share the
             same 5 dimensions in the same order).

    Returns dict: {
        "total": float 0-100,
        "raw": int 0-15,
        "dimensions": [ {key, left, right, desc, score_0_3, score_pct}, ... ]
    }
    """
    if len(answers) != 5:
        raise ValueError(f"expected 5 JI answers, got {len(answers)}")

    per_item = [JI_OPTION_SCORE[a.strip().lower()] for a in answers]
    raw = sum(per_item)
    total = raw * (100 / 15)

    dims = []
    for dim, score in zip(JI_DIMENSIONS, per_item):
        dims.append({
            **dim,
            "score_0_3": score,
            "score_pct": round(score / 3 * 100, 1),
        })

    return {"total": round(total, 1), "raw": raw, "dimensions": dims}


# ---------------------------------------------------------------------------
# Job Search (untargeted) -- A2 tailor, A3 tasks, A4 time, A5 scatter Likert
# A1 (application volume) is context only, never scored.
# ---------------------------------------------------------------------------

TAILOR_SCORE = {          # A2
    "0": 100, "1-2": 80, "3-5": 50, "6-8": 25, "9-10": 0,
    "not_yet_10": None,   # excluded from the mean, not scored as 0
}
TASKS_SCORE = {            # A3
    "none": 100, "1": 75, "2-3": 50, "4-5": 25, "more_than_5": 0,
}
TIME_SCORE = {              # A4
    "under_10": 100, "10_30": 66, "30_60": 33, "more_than_60": 0,
}


def _scatter_score(likert_1_5):
    """A5 -- Likert 1..5 -> 0/25/50/75/100"""
    v = int(likert_1_5)
    if not 1 <= v <= 5:
        raise ValueError("A5 must be a Likert value 1-5")
    return (v - 1) * 25


def score_job_search(a2_tailor, a3_tasks, a4_time, a5_scatter_likert):
    """
    a2_tailor: one of '0'|'1-2'|'3-5'|'6-8'|'9-10'|'not_yet_10'
    a3_tasks:  one of 'none'|'1'|'2-3'|'4-5'|'more_than_5'
    a4_time:   one of 'under_10'|'10_30'|'30_60'|'more_than_60'
    a5_scatter_likert: int 1-5

    Returns dict: {"total": float 0-100, "components": {...}}
    """
    tailor = TAILOR_SCORE[a2_tailor]
    tasks = TASKS_SCORE[a3_tasks]
    time_ = TIME_SCORE[a4_time]
    scatter = _scatter_score(a5_scatter_likert)

    components = {"tailor": tailor, "tasks": tasks, "time": time_, "scatter": scatter}
    usable = [v for v in components.values() if v is not None]
    total = mean(usable) if usable else None

    return {
        "total": round(total, 1) if total is not None else None,
        "components": components,
    }


# ---------------------------------------------------------------------------
# Control items C1-C3 -- validity check, not part of either axis score
# ---------------------------------------------------------------------------

def control_mean(c1, c2, c3):
    return round(mean([int(c1), int(c2), int(c3)]), 2)


def control_shift(mean_a, mean_b):
    """
    Absolute shift between two control means (e.g. Pre vs Post Survey 2).
    < 0.3  -> credible
    0.3-0.5 -> caveat
    > 0.5  -> likely acquiescence, discount the JI gain
    """
    delta = round(abs(mean_a - mean_b), 2)
    if delta < 0.3:
        verdict = "credible"
    elif delta <= 0.5:
        verdict = "caveat"
    else:
        verdict = "discount"
    return {"delta": delta, "verdict": verdict}


# ---------------------------------------------------------------------------
# Quadrant classification
# ---------------------------------------------------------------------------

def quadrant(job_search_score, job_intelligence_score, midpoint=50):
    """Returns one of the four quadrant labels used throughout the report."""
    high_search = job_search_score >= midpoint
    high_ji = job_intelligence_score >= midpoint
    if high_search and not high_ji:
        return "Volume Applicant"
    if high_search and high_ji:
        return "Busy Strategist"
    if not high_search and not high_ji:
        return "Drifting"
    return "Job Intelligent"

"""
Every question on every survey, in one place.

The three form templates render the questions; this module names them, so
that admin can show what a student actually answered -- "1-2 of my last 10"
rather than the stored code `1-2`, against the question that produced it.
The scenario definitions live here too, and the forms import them from here,
so section B has exactly one source of truth.

`tests/test_questions.py` walks the form templates and fails if a field
appears in one and not the other, which is what stops this drifting the next
time a question is reworded.
"""

SCENARIOS_PRE_WEEK4 = [
    {
        "name": "b1",
        "num": "B1",
        "text": "A friend tells you they want to become a Business Analyst. What is the most useful first question to ask them?",
        "options": [
            ("a", "Which companies near you are actively hiring for that role this year?"),
            ("b", "What skills and tools do most Business Analyst job postings ask for?"),
            ("c", "How is that role likely to look different five years from now?"),
            ("d", "What does a Business Analyst actually spend a typical week doing, and why?"),
        ]
    },
    {
        "name": "b2",
        "num": "B2",
        "text": "Two candidates apply for the same analyst role with identical degrees. Who is a hiring manager most likely to pick?",
        "options": [
            ("a", "The one whose university grades and transcript are the strongest"),
            ("b", "The one who lists the most tools and software on their resume"),
            ("c", "The one with the largest portfolio of finished personal projects"),
            ("d", "The one who can explain why a promising approach was wrong"),
        ]
    },
    {
        "name": "b3",
        "num": "B3",
        "text": "A company automates most of its routine content writing using AI. What new problem is this most likely to create?",
        "options": [
            ("a", "Not much changes, the work just gets done more cheaply"),
            ("b", "Most of the writing team will eventually be let go"),
            ("c", "They'll publish faster than anyone has time to check it"),
            ("d", "Someone still has to answer for what gets published and why"),
        ]
    },
    {
        "name": "b4",
        "num": "B4",
        "text": "You want to stand out for roles in the sports industry. Which is the strongest position to build?",
        "options": [
            ("a", "Learn a broad mix of sports-industry basics so you can adapt anywhere"),
            ("b", "Earn the most well-known certification that the sports industry recognises"),
            ("c", "Go deep on one in-demand skill, such as sports data analysis"),
            ("d", "Pair your sports knowledge with a second skill few others also have"),
        ]
    },
    {
        "name": "b5",
        "num": "B5",
        "text": "You're preparing for an interview and you use AI to help. Which use gives you the biggest real advantage?",
        "options": [
            ("a", "Ask it to draft full answers for you to learn by heart"),
            ("b", "Ask it for a list of typical questions you might be asked"),
            ("c", "Ask it to pull together background information about the company"),
            ("d", "Ask it to challenge your answers until you find where they break"),
        ]
    }
]

SCENARIOS_SAMEDAY = [
    {
        "name": "b1",
        "num": "B1",
        "text": "A friend tells you they want to become a Digital Marketer. What is the most useful first question to ask them?",
        "options": [
            ("a", "Which companies in your city are hiring for that role now?"),
            ("b", "What tools and platforms do most digital marketing job ads list?"),
            ("c", "Which parts of digital marketing are likely to look different soon?"),
            ("d", "What does a digital marketer actually spend most of their week doing?"),
        ]
    },
    {
        "name": "b2",
        "num": "B2",
        "text": "Two candidates apply for the same product role with identical qualifications. Who is more valuable to the company?",
        "options": [
            ("a", "The one whose university transcript shows the higher marks"),
            ("b", "The one who has hands-on experience with the most product tools"),
            ("c", "The one who already has more shipped side projects to show"),
            ("d", "The one who can defend a tough call they made"),
        ]
    },
    {
        "name": "b3",
        "num": "B3",
        "text": "A hospital starts using AI to draft patient discharge summaries. What new problem does this most likely create?",
        "options": [
            ("a", "Nothing much really changes, it just saves doctors some time"),
            ("b", "Junior doctors will simply have less writing work to do"),
            ("c", "Summaries pile up faster than staff can double-check them all"),
            ("d", "Someone still has to answer for a mistake in a summary"),
        ]
    },
    {
        "name": "b4",
        "num": "B4",
        "text": "You want to stand out in the finance industry. Which is the strongest position to build?",
        "options": [
            ("a", "Get a broad working knowledge of most areas in finance"),
            ("b", "Earn the single most respected certification finance professionals hold"),
            ("c", "Go deep on one specific skill, such as financial modelling work"),
            ("d", "Pair core finance skills with a second field few others know"),
        ]
    },
    {
        "name": "b5",
        "num": "B5",
        "text": "You're writing a business proposal and you use AI to help. Which use gives you the biggest real advantage?",
        "options": [
            ("a", "Have it write the whole proposal for you to send"),
            ("b", "Have it fix the grammar, spelling and formatting for you"),
            ("c", "Have it draft a rough version for you to rewrite"),
            ("d", "Have it poke holes in your logic until something breaks"),
        ]
    }
]


# The 1-5 agreement scales. Stored as the digit the student clicked.
_LIKERT = [(str(v), str(v)) for v in range(1, 6)]

# Identity, plumbing and scoring inputs that are not questions.
META_FIELDS = {"name", "email", "batch", "password", "new_password",
               "confirm_password", "fill_seconds", "t"}


def _scenario_questions(scenarios):
    return [
        {"key": sc["name"], "num": sc["num"], "text": sc["text"],
         "kind": "scenario", "options": list(sc["options"])}
        for sc in scenarios
    ]


def _q(key, num, text, kind, options=None):
    return {"key": key, "num": num, "text": text, "kind": kind,
            "options": list(options) if options else []}


_A_APPLICATIONS = [("0", "0"), ("1-5", "1–5"), ("6-15", "6–15"),
                   ("16-30", "16–30"), ("more_than_30", "More than 30")]
_A_TAILORED = [("0", "0"), ("1-2", "1–2"), ("3-5", "3–5"), ("6-8", "6–8"),
               ("9-10", "9–10"), ("not_yet_10", "Haven't applied to 10 yet")]
_A_TASKS = [("none", "None"), ("1", "1"), ("2-3", "2–3"), ("4-5", "4–5"),
            ("more_than_5", "More than 5")]
_A_TIME = [("under_10", "Under 10 minutes"), ("10_30", "10–30 minutes"),
           ("30_60", "30–60 minutes"), ("more_than_60", "More than an hour")]

_C_SENSE_CHECK = [
    _q("c1", "C1", "“Networking and referrals matter in getting a job.”", "likert", _LIKERT),
    _q("c2", "C2", "“I prefer working in a team over working alone.”", "likert", _LIKERT),
    _q("c3", "C3", "“I prefer detailed instructions over figuring things out myself.”", "likert", _LIKERT),
]


QUESTIONS = {
    "pre": [
        _q("a1", "A1", "Roughly how many job or internship applications have you submitted in the last 30 days?", "choice", _A_APPLICATIONS),
        _q("a2", "A2", "Think about your last 10 applications. For how many did you change something meaningful in your CV or cover letter?", "choice", _A_TAILORED),
        _q("a3", "A3", "For how many roles have you written down the specific tasks that role involves day to day?", "choice", _A_TASKS),
        _q("a4", "A4", "How much time do you typically spend preparing a single application?", "choice", _A_TIME),
        _q("a5", "A5", "“I apply to roles even for those that I'm not particularly interested in, to improve my overall chances.”", "likert", _LIKERT),
        *_scenario_questions(SCENARIOS_PRE_WEEK4),
        *_C_SENSE_CHECK,
        _q("d1", "D1", "What do you think is the biggest thing standing between you and the role you want?", "text"),
    ],
    "post_sameday": [
        *_scenario_questions(SCENARIOS_SAMEDAY),
        *_C_SENSE_CHECK,
        _q("d1", "D1", "Looking back now, how targeted was your job search before today?", "likert", _LIKERT),
        _q("d2", "D2", "“Before today, I had underestimated how untargeted my approach was.”", "likert", _LIKERT),
        _q("e1", "E1", "In the next two weeks, I intend to:", "multi", [
            ("tasks", "Break at least one target role into its constituent tasks in writing"),
            ("cv", "Rewrite my CV around problems I can solve rather than skills I have"),
            ("focus", "Reduce the number of roles I apply to and increase effort per application"),
            ("combo", "Identify a second skill to combine with my main one"),
            ("nothing", "Change nothing for now"),
        ]),
        _q("e2", "E2", "Name one specific market gap you identified today that you intend to explore further.", "text"),
        _q("f1", "F1", "What is now the biggest thing standing between you and the role you want?", "text"),
        _q("f2", "F2", "Which session changed your thinking the most?", "multi", [
            ("job_modularity", "Job Modularity"), ("industry_constraints", "Industry Constraints"),
            ("market_gaps", "Market Gaps"), ("ai_gap_assessment", "AI Gap Assessment"),
            ("industry_5_0", "Industry 5.0"),
        ]),
        _q("f3", "F3", "One thing that should be cut or expanded next time.", "text"),
    ],
    "post_week4": [
        _q("a1", "A1", "Roughly how many job or internship applications have you submitted in the last 30 days?", "choice", _A_APPLICATIONS),
        _q("a2", "A2", "Of your last 10 applications, for how many did you change something meaningful beyond the company name?", "choice", _A_TAILORED),
        _q("a3", "A3", "For how many roles have you written down the specific tasks the role involves?", "choice", _A_TASKS),
        _q("a4", "A4", "How much time do you typically spend preparing a single application?", "choice", _A_TIME),
        _q("a5", "A5", "“I apply to roles even for those that I'm not particularly interested in, to improve my overall chances.”", "likert", _LIKERT),
        *_scenario_questions(SCENARIOS_PRE_WEEK4),
        *_C_SENSE_CHECK,
        _q("d1", "D1", "Since the workshop, have you broken a specific role into its constituent tasks, in writing?", "choice", [("yes", "Yes"), ("no", "No")]),
        _q("d2", "D2", "If yes: which role, and name one task you concluded AI would take over.", "text"),
        _q("d3", "D3", "Have you changed anything about your CV or how you apply?", "choice", [
            ("significantly", "Yes, significantly"), ("slightly", "Yes, slightly"), ("no", "No")]),
        _q("d4", "D4", "If yes: what changed?", "text"),
        _q("e1", "E1", "Interviews attended in the last four weeks:", "choice", [("0", "0"), ("1", "1"), ("2-3", "2–3"), ("4+", "4+")]),
        _q("e2", "E2", "Offers received:", "choice", [("0", "0"), ("1", "1"), ("2+", "2+")]),
        _q("e3", "E3", "What is the biggest thing standing between you and the role you want?", "text"),
    ],
}


def answers_for(doc):
    """
    Every question on that stage paired with what this student answered, in
    the order they were asked. Unanswered questions are kept and marked, so
    the admin view shows a gap as a gap rather than by omitting the row.

    Returns a list of {num, text, kind, answer, raw, answered}. `answer` is
    the human-readable label -- the option text, not the stored code.
    """
    if not doc:
        return []
    raw = doc.get("raw_answers") or {}
    out = []
    for q in QUESTIONS.get(doc.get("stage"), []):
        value = raw.get(q["key"])
        labels = dict(q["options"])
        if isinstance(value, list):
            answer = ", ".join(labels.get(v, v) for v in value)
        elif value is None or str(value).strip() == "":
            answer = None
        elif q["kind"] == "likert":
            answer = f"{value} of 5"
        else:
            answer = labels.get(str(value), str(value))
        out.append({"num": q["num"], "text": q["text"], "kind": q["kind"],
                    "answer": answer, "raw": value, "answered": answer is not None})
    return out


def extra_fields(doc):
    """Anything stored in raw_answers that no longer maps to a question --
    a field from an older version of the form, say. Surfaced rather than
    silently dropped, so nothing a student typed is invisible in admin."""
    if not doc:
        return []
    raw = doc.get("raw_answers") or {}
    known = {q["key"] for q in QUESTIONS.get(doc.get("stage"), [])}
    return [(k, v) for k, v in sorted(raw.items())
            if k not in known and k not in META_FIELDS]

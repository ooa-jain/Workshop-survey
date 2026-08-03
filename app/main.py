import random
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, scoring, charts_svg, charts_email, email_utils, eligibility
from .config import settings

app = FastAPI(title=settings.APP_NAME)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

security = HTTPBasic()


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


def prep_scenarios(scenarios):
    """Return the scenarios in their fixed order (B1, B2, B3 ...), but with
    each one's answer options shuffled. The option *values* (the a-d letters
    the scorer keys on) are preserved -- only the on-screen order of the
    answers changes -- so scoring is unaffected while no two students see the
    same answer layout."""
    prepared = []
    for sc in scenarios:
        opts = list(sc["options"])
        random.shuffle(opts)
        prepared.append({**sc, "options": opts})
    return prepared


def fill_seconds(doc):
    """How long the student took to fill a survey, in seconds, or None if the
    submission predates time-tracking. Captured client-side and carried in
    raw_answers as 'fill_seconds'."""
    try:
        v = (doc.get("raw_answers") or {}).get("fill_seconds")
        return int(v) if v not in (None, "", []) else None
    except (ValueError, TypeError):
        return None


def fmt_duration(seconds):
    """Seconds -> a compact 'Xm YYs' / 'Ys' label, or an em dash when unknown."""
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


@app.on_event("startup")
def _startup():
    db.ensure_indexes()


def require(form, key, label=None):
    val = form.get(key)
    if val is None or str(val).strip() == "":
        raise HTTPException(status_code=400, detail=f"Missing required field: {label or key}")
    return val


def batch_from(request: Request):
    return request.query_params.get("batch", settings.WORKSHOP_BATCH)


def _status_url(batch, email):
    """The student's home/results page for this batch + email."""
    from urllib.parse import quote
    return f"/status?batch={quote(batch)}&email={quote(email)}"


def check_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, settings.ADMIN_USER)
    ok_pass = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def base_ctx(request, batch, **extra):
    ctx = {"request": request, "batch": batch, "app_name": settings.APP_NAME}
    ctx.update(extra)
    return ctx


def gate_or_none(stage, email, batch):
    """
    Server-side enforcement of the same rules the browser sees.

    Returns None when the student may proceed, or (status_dict, arc) when
    they may not. 'done' is never a blocker -- resubmitting overwrites,
    which is deliberate and documented in the README.
    """
    arc = db.get_student_arc(email, batch) if email else {}
    dev_mode = db.get_dev_mode()
    st = eligibility.stage_status(stage, arc, dev_mode=dev_mode)
    if st["state"] in ("locked", "expired"):
        return st, arc
    return None


def locked_page(request, stage, st, batch, email=None):
    meta = eligibility.STAGE_META[stage]
    return templates.TemplateResponse(request, "locked.html", base_ctx(
        request, batch, stage=stage, meta=meta, st=st, email=email or "",
    ))


def next_steps_for(email, batch):
    """The three-stage strip shown at the bottom of every result page, so a
    student who has just finished Pre immediately sees the same-day survey
    sitting open and the week-4 one counting down."""
    dev_mode = db.get_dev_mode()
    return eligibility.full_status(db.get_student_arc(email, batch), dev_mode=dev_mode)


def build_progress(arc):
    """Everything a student sees on their home page: each survey they've
    submitted with its scores, their growth from Pre to their latest survey,
    and the same trajectory + dimension charts the result pages draw. Returns
    None until there is at least one submission to show."""
    pre = arc.get("pre")
    sameday = arc.get("post_sameday")
    week4 = arc.get("post_week4")
    if not (pre or sameday or week4):
        return None

    def stage_row(label, doc, has_js):
        s = doc["scores"]
        return {
            "label": label,
            "date": doc["submitted_at"],
            "ji": s["job_intelligence"]["total"],
            "js": s.get("job_search", {}).get("total") if has_js else None,
            "quadrant": s.get("quadrant"),
        }

    rows = []
    if pre:
        rows.append(stage_row("Pre", pre, True))
    if sameday:
        rows.append(stage_row("Same day", sameday, False))
    if week4:
        rows.append(stage_row("Week 4", week4, True))

    # Growth: Pre -> the most recent survey that carries each score.
    ji_pre = pre["scores"]["job_intelligence"]["total"] if pre else None
    latest_ji = (week4 or sameday or pre)["scores"]["job_intelligence"]["total"]
    ji_growth = round(latest_ji - ji_pre, 1) if ji_pre is not None else None
    ji_growth_pct = (round((ji_growth / ji_pre) * 100) if (ji_pre and ji_growth is not None) else None)

    js_pre = pre["scores"]["job_search"]["total"] if pre else None
    latest_js = week4["scores"]["job_search"]["total"] if week4 else js_pre
    js_growth = round(latest_js - js_pre, 1) if (js_pre is not None and latest_js is not None and week4) else None

    quad_series = []
    if pre:
        quad_series.append({"label": "Pre", "job_search": js_pre, "job_intelligence": ji_pre})
    if sameday:
        quad_series.append({"label": "Same day", "job_search": None,
                            "job_intelligence": sameday["scores"]["job_intelligence"]["total"]})
    if week4:
        quad_series.append({"label": "Week 4", "job_search": week4["scores"]["job_search"]["total"],
                            "job_intelligence": week4["scores"]["job_intelligence"]["total"]})

    dim_svg_rows = []
    if pre and "dimensions" in pre["scores"]["job_intelligence"]:
        for i, dim in enumerate(pre["scores"]["job_intelligence"]["dimensions"]):
            pts = [{"label": "Pre", "score_0_3": dim["score_0_3"]}]
            if sameday:
                pts.append({"label": "Same day",
                            "score_0_3": sameday["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
            if week4:
                pts.append({"label": "Week 4",
                            "score_0_3": week4["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
            dim_svg_rows.append(charts_svg.dimension_arrow_row(dim["desc"], dim["left"], dim["right"], pts))

    return {
        "rows": rows,
        "latest_label": rows[-1]["label"],
        "ji_pre": ji_pre, "ji_latest": latest_ji, "ji_growth": ji_growth, "ji_growth_pct": ji_growth_pct,
        "js_pre": js_pre, "js_latest": latest_js, "js_growth": js_growth,
        "quad_svg": charts_svg.quadrant_svg(quad_series) if quad_series else None,
        "dim_svg_rows": dim_svg_rows,
    }


# ---------------------------------------------------------------------------
# Landing + student status
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    batch = batch_from(request)
    dev_mode = db.get_dev_mode()
    return templates.TemplateResponse(request, "home.html", base_ctx(
        request, batch, stages=eligibility.full_status({}, dev_mode=dev_mode),
        unlock_days=settings.WEEK4_UNLOCK_DAYS,
    ))


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    """Student-facing 'where am I' page. No password: it only ever reveals
    which of three surveys you have filled in, for an email you typed in
    yourself."""
    batch = batch_from(request)
    email = (request.query_params.get("email") or "").strip()
    if not email:
        return templates.TemplateResponse(request, "status.html", base_ctx(
            request, batch, email="", stages=None, name=None, found=False,
        ))
    arc = db.get_student_arc(email, batch)
    name = arc["pre"]["name"] if arc.get("pre") else None
    dev_mode = db.get_dev_mode()
    return templates.TemplateResponse(request, "status.html", base_ctx(
        request, batch, email=email, name=name,
        stages=eligibility.full_status(arc, dev_mode=dev_mode),
        found=bool(arc),
        progress=build_progress(arc),
    ))


@app.get("/api/check")
def api_check(request: Request):
    """Called by the stepper as soon as a student types their email on the
    identity step, so they find out a stage is locked before answering
    twenty questions rather than after."""
    stage = request.query_params.get("stage", "pre")
    if stage not in eligibility.STAGES:
        raise HTTPException(status_code=400, detail="Unknown stage")
    batch = batch_from(request)
    email = (request.query_params.get("email") or "").strip()
    arc = db.get_student_arc(email, batch) if email else {}
    dev_mode = db.get_dev_mode()
    st = eligibility.stage_status(stage, arc, dev_mode=dev_mode)
    name = arc["pre"]["name"] if arc.get("pre") else None
    
    # Check if student has password
    has_password = bool(arc.get("pre", {}).get("password_hash"))
    
    return JSONResponse({
        "ok": st["state"] in ("open", "done"),
        "state": st["state"],
        "headline": st["headline"],
        "detail": st["detail"],
        "name": name,
        "status_url": f"/status?batch={batch}&email={email}",
        "has_password": has_password,
    })


@app.post("/api/verify-password")
async def api_verify_password(request: Request):
    data = await request.json()
    email = data.get("email", "").strip()
    batch = data.get("batch", "").strip()
    password = data.get("password", "")

    if not email or not batch or not password:
        return JSONResponse({"ok": False, "detail": "Missing email, batch, or password"}, status_code=400)

    pre_doc = db.get_response(email, "pre", batch)
    if not pre_doc or not pre_doc.get("password_hash"):
        return JSONResponse({"ok": False, "detail": "No password set for this student."}, status_code=404)

    from . import auth
    if auth.verify_password(password, pre_doc["password_hash"], pre_doc["password_salt"]):
        return JSONResponse({"ok": True})
    else:
        return JSONResponse({"ok": False, "detail": "Incorrect password"})


@app.post("/api/reset-password")
async def api_reset_password(request: Request):
    data = await request.json()
    email = data.get("email", "").strip()
    batch = data.get("batch", "").strip()
    new_password = data.get("new_password", "")

    if not email or not batch or not new_password:
        return JSONResponse({"ok": False, "detail": "Missing email, batch, or new password"}, status_code=400)

    pre_doc = db.get_response(email, "pre", batch)
    if not pre_doc:
        return JSONResponse({"ok": False, "detail": "No Pre survey found for this email. Please complete the Pre survey first."}, status_code=404)

    from . import auth
    pwd_hash, pwd_salt = auth.hash_password(new_password)
    db.update_student_password(email, batch, pwd_hash, pwd_salt)
    return JSONResponse({"ok": True})



# ---------------------------------------------------------------------------
# PRE
# ---------------------------------------------------------------------------

@app.get("/survey/pre", response_class=HTMLResponse)
def pre_form(request: Request):
    batch = batch_from(request)
    email = (request.query_params.get("email") or "").strip()
    # Pre is write-once. If this student has already submitted it, there's
    # nothing to edit -- send them to their results/home page instead of the
    # form, so a "Pre" click only ever shows results.
    if email and db.get_response(email, "pre", batch):
        return RedirectResponse(_status_url(batch, email), status_code=303)
    b_scenarios = prep_scenarios(SCENARIOS_PRE_WEEK4)
    return templates.TemplateResponse(request, "pre.html", base_ctx(
        request, batch,
        prefill_email=email,
        prefill_name="",
        b_scenarios=b_scenarios,
        autofill=db.get_dev_mode(),
    ))


@app.post("/survey/pre", response_class=HTMLResponse)
async def pre_submit(request: Request):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)

    name = require(form, "name", "Name")
    email = require(form, "email", "Email")
    password = require(form, "password", "Password")

    # Pre is write-once. Once a student has a Pre on record it can't be
    # edited or overwritten -- bounce them to their results page. Their
    # password is set here, on that first (and only) submission, and reused
    # to gate the Week-4 survey.
    pre_doc = db.get_response(email, "pre", batch)
    if pre_doc:
        return RedirectResponse(_status_url(batch, email), status_code=303)

    from . import auth
    pwd_hash, pwd_salt = auth.hash_password(password)

    b_answers = [require(form, f"b{i}", f"Scenario B{i}") for i in range(1, 6)]
    ji = scoring.score_job_intelligence(b_answers)

    a2, a3, a4 = require(form, "a2"), require(form, "a3"), require(form, "a4")
    a5 = require(form, "a5", "A5")
    js = scoring.score_job_search(a2, a3, a4, a5)

    c1, c2, c3 = require(form, "c1"), require(form, "c2"), require(form, "c3")
    control = scoring.control_mean(c1, c2, c3)

    quad = scoring.quadrant(js["total"], ji["total"])

    raw = {k: v for k, v in form.multi_items()}
    scores = {
        "job_intelligence": ji,
        "job_search": js,
        "control_mean": control,
        "quadrant": quad,
    }
    db.upsert_response(email, name, "pre", batch, raw, scores, password_hash=pwd_hash, password_salt=pwd_salt)

    _send_pre_email(name, email, ji, js, control, quad)

    return templates.TemplateResponse(request, "result_pre.html", base_ctx(
        request, batch, name=name, email=email,
        ji=ji, js=js, quadrant=quad,
        dim_svg_rows=[
            charts_svg.dimension_arrow_row(
                d["desc"], d["left"], d["right"],
                [{"label": "Pre", "score_0_3": d["score_0_3"]}])
            for d in ji["dimensions"]
        ],
        quad_svg=charts_svg.quadrant_svg(
            [{"label": "Pre", "job_search": js["total"], "job_intelligence": ji["total"]}]),
        next_steps=next_steps_for(email, batch),
    ))


def _send_pre_email(name, email, ji, js, control, quad):
    rows_for_png = [{"desc": d["desc"], "left": d["left"], "right": d["right"],
                      "points": [{"label": "Pre", "score_0_3": d["score_0_3"]}]} for d in ji["dimensions"]]
    dim_png = charts_email.dimension_arrows_png(rows_for_png)
    quad_png = charts_email.quadrant_png([{"label": "Pre", "job_search": js["total"], "job_intelligence": ji["total"]}])

    html = templates.get_template("email_pre.html").render(
        name=name, ji=ji, js=js, quadrant=quad, app_name=settings.APP_NAME,
    )
    email_utils.send_result_email(
        email, name, f"{settings.APP_NAME} — your starting point", html,
        {"dim_chart": dim_png, "quad_chart": quad_png},
    )


# ---------------------------------------------------------------------------
# SAME-DAY POST
# ---------------------------------------------------------------------------

@app.get("/survey/post-sameday", response_class=HTMLResponse)
def sameday_form(request: Request):
    batch = batch_from(request)
    email = (request.query_params.get("email") or "").strip()
    if email:
        blocked = gate_or_none("post_sameday", email, batch)
        if blocked:
            return locked_page(request, "post_sameday", blocked[0], batch, email)
    b_scenarios = prep_scenarios(SCENARIOS_SAMEDAY)
    return templates.TemplateResponse(request, "post_sameday.html", base_ctx(
        request, batch, prefill_email=email, prefill_name="",
        b_scenarios=b_scenarios,
        autofill=db.get_dev_mode(),
    ))


@app.post("/survey/post-sameday", response_class=HTMLResponse)
async def sameday_submit(request: Request):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)

    name = require(form, "name", "Name")
    email = require(form, "email", "Email")

    blocked = gate_or_none("post_sameday", email, batch)
    if blocked:
        return locked_page(request, "post_sameday", blocked[0], batch, email)

    # The same-day survey no longer asks for a password -- it's a low-stakes
    # check-in and the email alone matches it to the Pre response.
    pre_doc = db.get_response(email, "pre", batch)

    b_answers = [require(form, f"b{i}", f"Scenario B{i}") for i in range(1, 6)]
    ji = scoring.score_job_intelligence(b_answers)

    c1, c2, c3 = require(form, "c1"), require(form, "c2"), require(form, "c3")
    control = scoring.control_mean(c1, c2, c3)

    raw = {}
    for k, v in form.multi_items():
        if k in raw:
            if isinstance(raw[k], list):
                raw[k].append(v)
            else:
                raw[k] = [raw[k], v]
        else:
            raw[k] = v

    scores = {"job_intelligence": ji, "control_mean": control, "matched_pre": bool(pre_doc)}
    if pre_doc:
        pre_scores = pre_doc["scores"]
        scores["control_shift_vs_pre"] = scoring.control_shift(pre_scores["control_mean"], control)
        scores["ji_delta_vs_pre"] = round(ji["total"] - pre_scores["job_intelligence"]["total"], 1)

    db.upsert_response(email, name, "post_sameday", batch, raw, scores)

    quad_series = None
    dim_rows_svg = []
    dim_rows_png = []
    if pre_doc:
        pre_ji = pre_doc["scores"]["job_intelligence"]
        pre_js = pre_doc["scores"]["job_search"]
        quad_series = [
            {"label": "Pre", "job_search": pre_js["total"], "job_intelligence": pre_ji["total"]},
            {"label": "Same day", "job_search": None, "job_intelligence": ji["total"]},
        ]
        for i, dim in enumerate(ji["dimensions"]):
            pts = [{"label": "Pre", "score_0_3": pre_ji["dimensions"][i]["score_0_3"]},
                   {"label": "Same day", "score_0_3": dim["score_0_3"]}]
            dim_rows_svg.append(charts_svg.dimension_arrow_row(dim["desc"], dim["left"], dim["right"], pts))
            dim_rows_png.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"], "points": pts})
    else:
        for dim in ji["dimensions"]:
            pts = [{"label": "Today", "score_0_3": dim["score_0_3"]}]
            dim_rows_svg.append(charts_svg.dimension_arrow_row(dim["desc"], dim["left"], dim["right"], pts))
            dim_rows_png.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"], "points": pts})

    _send_sameday_email(name, email, ji, quad_series, dim_rows_png)

    return templates.TemplateResponse(request, "result_sameday.html", base_ctx(
        request, batch, name=name, email=email, ji=ji,
        matched_pre=bool(pre_doc),
        ji_delta=scores.get("ji_delta_vs_pre"),
        dim_svg_rows=dim_rows_svg,
        quad_svg=charts_svg.quadrant_svg(quad_series) if quad_series else None,
        next_steps=next_steps_for(email, batch),
    ))


def _send_sameday_email(name, email, ji, quad_series, dim_rows_png):
    dim_png = charts_email.dimension_arrows_png(dim_rows_png)
    quad_png = charts_email.quadrant_png(quad_series) if quad_series else None
    images = {"dim_chart": dim_png}
    if quad_png:
        images["quad_chart"] = quad_png

    html = templates.get_template("email_sameday.html").render(
        name=name, ji=ji, app_name=settings.APP_NAME, has_quad=bool(quad_png),
        week4_days=settings.WEEK4_UNLOCK_DAYS,
    )
    email_utils.send_result_email(
        email, name, f"{settings.APP_NAME} — how today shifted your thinking", html, images,
    )


# ---------------------------------------------------------------------------
# 4-WEEK POST
# ---------------------------------------------------------------------------

@app.get("/survey/post-week4", response_class=HTMLResponse)
def week4_form(request: Request):
    batch = batch_from(request)
    email = (request.query_params.get("email") or "").strip()
    token = request.query_params.get("t")

    prefill_name = ""
    if email:
        blocked = gate_or_none("post_week4", email, batch)
        if blocked:
            return locked_page(request, "post_week4", blocked[0], batch, email)
        # A valid signed link from the reminder mail also prefills the name,
        # so the student only has to answer questions.
        if eligibility.check_token(token, email, batch):
            pre = db.get_response(email, "pre", batch)
            if pre:
                prefill_name = pre["name"]

    b_scenarios = prep_scenarios(SCENARIOS_PRE_WEEK4)
    return templates.TemplateResponse(request, "post_week4.html", base_ctx(
        request, batch, prefill_email=email, prefill_name=prefill_name,
        b_scenarios=b_scenarios,
        autofill=db.get_dev_mode(),
    ))


@app.post("/survey/post-week4", response_class=HTMLResponse)
async def week4_submit(request: Request):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)

    name = require(form, "name", "Name")
    email = require(form, "email", "Email")
    password = require(form, "password", "Password")

    blocked = gate_or_none("post_week4", email, batch)
    if blocked:
        return locked_page(request, "post_week4", blocked[0], batch, email)

    pre_doc = db.get_response(email, "pre", batch)
    if pre_doc and pre_doc.get("password_hash"):
        from . import auth
        if not auth.verify_password(password, pre_doc["password_hash"], pre_doc["password_salt"]):
            b_scenarios = prep_scenarios(SCENARIOS_PRE_WEEK4)
            return templates.TemplateResponse(request, "post_week4.html", base_ctx(
                request, batch, prefill_email=email, prefill_name=name,
                error_msg="Incorrect password.",
                b_scenarios=b_scenarios,
            ))

    b_answers = [require(form, f"b{i}", f"Scenario B{i}") for i in range(1, 6)]
    ji = scoring.score_job_intelligence(b_answers)

    a2, a3, a4 = require(form, "a2"), require(form, "a3"), require(form, "a4")
    a5 = require(form, "a5", "A5")
    js = scoring.score_job_search(a2, a3, a4, a5)

    c1, c2, c3 = require(form, "c1"), require(form, "c2"), require(form, "c3")
    control = scoring.control_mean(c1, c2, c3)

    quad = scoring.quadrant(js["total"], ji["total"])

    raw = {k: v for k, v in form.multi_items()}
    sameday_doc = db.get_response(email, "post_sameday", batch)

    scores = {
        "job_intelligence": ji, "job_search": js, "control_mean": control, "quadrant": quad,
        "matched_pre": bool(pre_doc),
    }
    if pre_doc:
        pre_scores = pre_doc["scores"]
        scores["control_shift_vs_pre"] = scoring.control_shift(pre_scores["control_mean"], control)
        scores["ji_delta_vs_pre"] = round(ji["total"] - pre_scores["job_intelligence"]["total"], 1)
        scores["job_search_delta_vs_pre"] = round(js["total"] - pre_scores["job_search"]["total"], 1)

    db.upsert_response(email, name, "post_week4", batch, raw, scores)

    quad_series = [{"label": "Week 4", "job_search": js["total"], "job_intelligence": ji["total"]}]
    dim_points_by_dim = [[{"label": "Week 4", "score_0_3": d["score_0_3"]}] for d in ji["dimensions"]]

    if pre_doc:
        pre_ji = pre_doc["scores"]["job_intelligence"]
        pre_js = pre_doc["scores"]["job_search"]
        quad_series = [{"label": "Pre", "job_search": pre_js["total"], "job_intelligence": pre_ji["total"]}]
        if sameday_doc:
            quad_series.append({"label": "Same day", "job_search": None,
                                 "job_intelligence": sameday_doc["scores"]["job_intelligence"]["total"]})
        quad_series.append({"label": "Week 4", "job_search": js["total"], "job_intelligence": ji["total"]})

        for i, d in enumerate(ji["dimensions"]):
            pts = [{"label": "Pre", "score_0_3": pre_ji["dimensions"][i]["score_0_3"]}]
            if sameday_doc:
                pts.append({"label": "Same day",
                            "score_0_3": sameday_doc["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
            pts.append({"label": "Week 4", "score_0_3": d["score_0_3"]})
            dim_points_by_dim[i] = pts

    dim_rows_svg = [
        charts_svg.dimension_arrow_row(d["desc"], d["left"], d["right"], dim_points_by_dim[i])
        for i, d in enumerate(ji["dimensions"])
    ]
    dim_rows_png = [
        {"desc": d["desc"], "left": d["left"], "right": d["right"], "points": dim_points_by_dim[i]}
        for i, d in enumerate(ji["dimensions"])
    ]

    _send_week4_email(name, email, ji, js, quad, quad_series, dim_rows_png, scores)

    return templates.TemplateResponse(request, "result_week4.html", base_ctx(
        request, batch, name=name, email=email, ji=ji, js=js,
        quadrant=quad, matched_pre=bool(pre_doc),
        ji_delta=scores.get("ji_delta_vs_pre"),
        job_search_delta=scores.get("job_search_delta_vs_pre"),
        dim_svg_rows=dim_rows_svg,
        quad_svg=charts_svg.quadrant_svg(quad_series),
        next_steps=next_steps_for(email, batch),
    ))


def _send_week4_email(name, email, ji, js, quad, quad_series, dim_rows_png, scores):
    dim_png = charts_email.dimension_arrows_png(dim_rows_png)
    quad_png = charts_email.quadrant_png(quad_series)

    html = templates.get_template("email_week4.html").render(
        name=name, ji=ji, js=js, quadrant=quad, app_name=settings.APP_NAME,
        ji_delta=scores.get("ji_delta_vs_pre"), job_search_delta=scores.get("job_search_delta_vs_pre"),
    )
    email_utils.send_result_email(
        email, name, f"{settings.APP_NAME} — your four-week result", html,
        {"dim_chart": dim_png, "quad_chart": quad_png},
    )


# ---------------------------------------------------------------------------
# ADMIN DASHBOARD
# ---------------------------------------------------------------------------

def _avg(vals, digits=1):
    return round(sum(vals) / len(vals), digits) if vals else None


def build_cohort_analysis(batch):
    """Two aggregate views the admin sees on both the Results and Groups
    pages, computed once from the same DB reads:

      outcome  -- "first day": how the cohort moved Pre -> Same-day, for every
                  student who filled both. Same-day carries no Job-Search
                  score, so this is a Job-Intelligence movement story.
      impact   -- "four weeks on": the full Pre -> Week-4 picture, every
                  student's arrow on the quadrant field plus the dimension,
                  trajectory and credibility analysis.
    """
    # ---- Outcome: Pre -> Same-day -----------------------------------------
    sd_matched = db.matched_sameday(batch)
    sd_pre = [m["pre"]["scores"]["job_intelligence"]["total"] for m in sd_matched]
    sd_now = [m["sameday"]["scores"]["job_intelligence"]["total"] for m in sd_matched]
    sd_dim_rows = []
    for i, dim in enumerate(scoring.JI_DIMENSIONS):
        deltas = [
            m["sameday"]["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]
            - m["pre"]["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]
            for m in sd_matched
        ]
        sd_dim_rows.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"],
                            "mean_delta": round(sum(deltas) / len(deltas), 2) if deltas else 0.0})
    sd_slope = [{"ji_pre": m["pre"]["scores"]["job_intelligence"]["total"],
                 "ji_w4": m["sameday"]["scores"]["job_intelligence"]["total"]} for m in sd_matched]

    outcome = {
        "has_data": bool(sd_matched),
        "n": len(sd_matched),
        "ji_pre_mean": _avg(sd_pre),
        "ji_latest_mean": _avg(sd_now),
        "ji_delta": (round(_avg(sd_now) - _avg(sd_pre), 1) if sd_matched else None),
        "bars_svg": charts_svg.diverging_bars_svg(sd_dim_rows, span_label="pre → same day") if sd_matched else None,
        "slope_svg": charts_svg.slopegraph_svg(sd_slope, col_labels=["Pre", "Same day"]) if sd_matched else None,
    }

    # ---- Impact: Pre -> Week-4 --------------------------------------------
    matched = db.matched_students(batch)
    students_for_charts = [{
        "job_search_pre": m["pre"]["scores"]["job_search"]["total"],
        "ji_pre": m["pre"]["scores"]["job_intelligence"]["total"],
        "job_search_w4": m["week4"]["scores"]["job_search"]["total"],
        "ji_w4": m["week4"]["scores"]["job_intelligence"]["total"],
    } for m in matched]

    quad_counts_pre = {"Volume Applicant": 0, "Busy Strategist": 0, "Drifting": 0, "Job Intelligent": 0}
    quad_counts_w4 = dict(quad_counts_pre)
    for m in matched:
        quad_counts_pre[m["pre"]["scores"]["quadrant"]] += 1
        quad_counts_w4[m["week4"]["scores"]["quadrant"]] += 1

    dim_rows = []
    for i, dim in enumerate(scoring.JI_DIMENSIONS):
        deltas = [
            m["week4"]["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]
            - m["pre"]["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]
            for m in matched
        ]
        dim_rows.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"],
                         "mean_delta": round(sum(deltas) / len(deltas), 2) if deltas else 0.0})

    slope_students = [{"ji_pre": m["pre"]["scores"]["job_intelligence"]["total"],
                       "ji_w4": m["week4"]["scores"]["job_intelligence"]["total"]} for m in matched]

    control_deltas = [
        scoring.control_shift(m["pre"]["scores"]["control_mean"], m["week4"]["scores"]["control_mean"])["delta"]
        for m in matched
    ]
    mean_control_shift = round(sum(control_deltas) / len(control_deltas), 2) if control_deltas else 0.0

    impact = {
        "has_data": bool(matched),
        "n": len(matched),
        "quad_counts_pre": quad_counts_pre,
        "quad_counts_w4": quad_counts_w4,
        "mean_control_shift": mean_control_shift,
        "field_svg": charts_svg.quadrant_field_svg(students_for_charts),
        "bars_svg": charts_svg.diverging_bars_svg(dim_rows),
        "slope_svg": charts_svg.slopegraph_svg(slope_students, has_sameday=False),
    }

    return {"outcome": outcome, "impact": impact}


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, username: str = Depends(check_admin)):
    batch = batch_from(request)

    pre_all = db.all_responses(batch, "pre")
    sameday_all = db.all_responses(batch, "post_sameday")
    week4_all = db.all_responses(batch, "post_week4")

    n_due = sum(1 for s in db.cohort_arcs(batch) if eligibility.is_due_for_week4_reminder(s["arc"]))
    analysis = build_cohort_analysis(batch)

    return templates.TemplateResponse(request, "admin_dashboard.html", base_ctx(
        request, batch,
        n_pre=len(pre_all), n_sameday=len(sameday_all), n_week4=len(week4_all),
        n_matched=analysis["impact"]["n"], n_due=n_due,
        analysis=analysis,
    ))


@app.get("/admin/student", response_class=HTMLResponse)
def admin_student(request: Request, username: str = Depends(check_admin)):
    batch = batch_from(request)
    cohort = db.cohort_arcs(batch)

    students_list = []
    for s in cohort:
        students_list.append({
            "name": s["name"],
            "email": s["email"],
            "has_pre": "pre" in s["arc"],
            "has_sameday": "post_sameday" in s["arc"],
            "has_week4": "post_week4" in s["arc"],
        })
    students_list.sort(key=lambda x: x["name"].lower())

    dev_mode = db.get_dev_mode()
    n_due = sum(1 for s in cohort if eligibility.is_due_for_week4_reminder(s["arc"], dev_mode=dev_mode))

    selected_email = (request.query_params.get("email") or "").strip()
    if not selected_email and students_list:
        selected_email = students_list[0]["email"]

    student_data = None
    if selected_email:
        arc = db.get_student_arc(selected_email, batch)
        pre = arc.get("pre")
        sameday = arc.get("post_sameday")
        week4 = arc.get("post_week4")

        if pre or sameday or week4:
            name = (pre or sameday or week4).get("name", selected_email)

            ji_latest = ji_pre = js_latest = js_pre = quad_latest = quad_pre = None
            control_pre = control_latest = None

            if pre:
                pre_s = pre["scores"]
                ji_pre = ji_latest = pre_s["job_intelligence"]["total"]
                js_pre = js_latest = pre_s["job_search"]["total"]
                quad_pre = quad_latest = pre_s.get("quadrant")
                control_pre = control_latest = pre_s.get("control_mean")

            if sameday:
                sd_s = sameday["scores"]
                ji_latest = sd_s["job_intelligence"]["total"]
                control_latest = sd_s.get("control_mean")

            if week4:
                w4_s = week4["scores"]
                ji_latest = w4_s["job_intelligence"]["total"]
                js_latest = w4_s["job_search"]["total"]
                quad_latest = w4_s.get("quadrant")
                control_latest = w4_s.get("control_mean")

            ji_delta = round(ji_latest - ji_pre, 1) if (ji_latest is not None and ji_pre is not None) else None
            js_delta = round(js_latest - js_pre, 1) if (js_latest is not None and js_pre is not None) else None
            control_shift = scoring.control_shift(control_pre, control_latest) if (control_pre is not None and control_latest is not None) else None

            dim_svg_rows = []
            if pre and "dimensions" in pre["scores"]["job_intelligence"]:
                pre_ji = pre["scores"]["job_intelligence"]
                for i, dim in enumerate(pre_ji["dimensions"]):
                    pts = [{"label": "Pre", "score_0_3": dim["score_0_3"]}]
                    if sameday and "scores" in sameday and "job_intelligence" in sameday["scores"] and "dimensions" in sameday["scores"]["job_intelligence"]:
                        pts.append({"label": "Same day", "score_0_3": sameday["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
                    if week4 and "scores" in week4 and "job_intelligence" in week4["scores"] and "dimensions" in week4["scores"]["job_intelligence"]:
                        pts.append({"label": "Week 4", "score_0_3": week4["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
                    dim_svg_rows.append(charts_svg.dimension_arrow_row(dim["desc"], dim["left"], dim["right"], pts))

            quad_series = []
            if pre and js_pre is not None and ji_pre is not None:
                quad_series.append({"label": "Pre", "job_search": js_pre, "job_intelligence": ji_pre})
            if sameday and "scores" in sameday and "job_intelligence" in sameday["scores"]:
                quad_series.append({"label": "Same day", "job_search": None, "job_intelligence": sameday["scores"]["job_intelligence"]["total"]})
            if week4 and "scores" in week4 and "job_search" in week4["scores"] and "job_intelligence" in week4["scores"]:
                quad_series.append({"label": "Week 4", "job_search": week4["scores"]["job_search"]["total"], "job_intelligence": week4["scores"]["job_intelligence"]["total"]})

            quad_svg = charts_svg.quadrant_svg(quad_series) if len(quad_series) >= 1 else None

            student_data = {
                "name": name,
                "email": selected_email,
                "pre": pre,
                "sameday": sameday,
                "week4": week4,
                "ji_latest": ji_latest,
                "ji_delta": ji_delta,
                "js_latest": js_latest,
                "js_delta": js_delta,
                "quad_pre": quad_pre,
                "quad_latest": quad_latest,
                "control_shift": control_shift,
                "dim_svg_rows": dim_svg_rows,
                "quad_svg": quad_svg,
                "pre_time": fmt_duration(fill_seconds(pre)) if pre else None,
                "sameday_time": fmt_duration(fill_seconds(sameday)) if sameday else None,
                "week4_time": fmt_duration(fill_seconds(week4)) if week4 else None,
            }

    return templates.TemplateResponse(request, "admin_student.html", base_ctx(
        request, batch,
        students=students_list,
        selected_email=selected_email,
        student=student_data,
        n_due=n_due,
    ))


# ---------------------------------------------------------------------------
# ADMIN -- GROUPS (by fill date) + time-to-fill
# ---------------------------------------------------------------------------

STAGE_LABEL = {"pre": "Pre", "post_sameday": "Same-day", "post_week4": "Week 4"}


@app.get("/admin/groups", response_class=HTMLResponse)
def admin_groups(request: Request, username: str = Depends(check_admin)):
    """Every submission grouped by the calendar day it was filled, with
    per-day counts, time-to-fill, and a delete-the-whole-day action. Also
    shows the overall totals across the batch."""
    from collections import defaultdict
    batch = batch_from(request)
    docs = db.all_responses(batch)

    by_day = defaultdict(list)
    for d in docs:
        by_day[d["submitted_at"].strftime("%Y-%m-%d")].append(d)

    groups = []
    for day in sorted(by_day.keys(), reverse=True):
        gdocs = by_day[day]
        counts = {"pre": 0, "post_sameday": 0, "post_week4": 0}
        times, people = [], []
        for d in gdocs:
            counts[d["stage"]] = counts.get(d["stage"], 0) + 1
            fs = fill_seconds(d)
            if fs is not None:
                times.append(fs)
            people.append({
                "name": d["name"], "email": d["email"],
                "stage": STAGE_LABEL.get(d["stage"], d["stage"]),
                "time": fmt_duration(fs),
                "at": d["submitted_at"].strftime("%H:%M"),
            })
        people.sort(key=lambda p: (p["name"].lower(), p["at"]))
        groups.append({
            "date": day,
            "date_label": gdocs[0]["submitted_at"].strftime("%d %b %Y"),
            "n_students": len({d["email_norm"] for d in gdocs}),
            "counts": counts,
            "total": len(gdocs),
            "avg_time": fmt_duration(sum(times) / len(times)) if times else "—",
            "people": people,
        })

    all_times = [t for t in (fill_seconds(d) for d in docs) if t is not None]
    overall = {
        "n_students": len({d["email_norm"] for d in docs}),
        "n_pre": sum(1 for d in docs if d["stage"] == "pre"),
        "n_sameday": sum(1 for d in docs if d["stage"] == "post_sameday"),
        "n_week4": sum(1 for d in docs if d["stage"] == "post_week4"),
        "total": len(docs),
        "avg_time": fmt_duration(sum(all_times) / len(all_times)) if all_times else "—",
    }

    dev_mode = db.get_dev_mode()
    n_due = sum(1 for s in db.cohort_arcs(batch)
                if eligibility.is_due_for_week4_reminder(s["arc"], dev_mode=dev_mode))

    return templates.TemplateResponse(request, "admin_groups.html", base_ctx(
        request, batch, groups=groups, overall=overall, n_due=n_due,
        analysis=build_cohort_analysis(batch),
        deleted=request.query_params.get("deleted"),
    ))


@app.post("/admin/groups/delete")
async def admin_groups_delete(request: Request, username: str = Depends(check_admin)):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    date = require(form, "date", "Date")
    deleted = db.delete_responses_on_date(batch, date)
    return RedirectResponse(f"/admin/groups?batch={batch}&deleted={deleted}", status_code=303)


# ---------------------------------------------------------------------------
# ADMIN -- WEEK-4 REMINDERS
# ---------------------------------------------------------------------------

def _reminder_rows(batch, now=None):
    """Every student with a Pre, split into: due a reminder, still waiting
    out the 30 days, and already finished."""
    now = now or datetime.now(timezone.utc)
    dev_mode = db.get_dev_mode()
    due, waiting, complete = [], [], []
    for s in db.cohort_arcs(batch):
        arc = s["arc"]
        pre = arc["pre"]
        st = eligibility.stage_status("post_week4", arc, now=now, dev_mode=dev_mode)
        unlock = eligibility.week4_unlock_at(pre)
        manual_access = bool(pre.get("manual_week4_access"))
        row = {
            "name": s["name"],
            "email": s["email"],
            "pre_at": pre["submitted_at"],
            "unlock_at": unlock,
            "days_left": eligibility.days_until(unlock, now),
            "sameday_done": "post_sameday" in arc,
            "state": st["state"],
            "headline": st["headline"],
            "reminder_count": pre.get("week4_reminder_count", 0),
            "reminder_last": pre.get("week4_reminder_last"),
            "manual_access": manual_access,
            "link": eligibility.week4_link(s["email"], batch),
        }
        if "post_week4" in arc:
            complete.append(row)
        elif eligibility.is_due_for_week4_reminder(arc, now=now, dev_mode=dev_mode):
            due.append(row)
        else:
            waiting.append(row)
    due.sort(key=lambda r: (r["reminder_count"], r["name"].lower()))
    waiting.sort(key=lambda r: r["days_left"])
    complete.sort(key=lambda r: r["name"].lower())
    return due, waiting, complete


@app.get("/admin/reminders", response_class=HTMLResponse)
def admin_reminders(request: Request, username: str = Depends(check_admin)):
    batch = batch_from(request)
    dev_mode = db.get_dev_mode()
    due, waiting, complete = _reminder_rows(batch)
    return templates.TemplateResponse(request, "admin_reminders.html", base_ctx(
        request, batch, due=due, waiting=waiting, complete=complete,
        unlock_days=settings.WEEK4_UNLOCK_DAYS,
        open_days=settings.WEEK4_OPEN_DAYS,
        email_live=settings.EMAIL_ENABLED and bool(settings.SMTP_HOST),
        dev_mode=dev_mode,
        sent=request.query_params.get("sent"),
        failed=request.query_params.get("failed"),
    ))


@app.post("/admin/reminders/dev-mode")
async def admin_toggle_dev_mode(request: Request, username: str = Depends(check_admin)):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    enabled = form.get("enabled") == "true"
    db.set_dev_mode(enabled)
    return RedirectResponse(f"/admin/reminders?batch={batch}", status_code=303)


@app.post("/admin/reminders/grant-access")
async def admin_grant_access(request: Request, username: str = Depends(check_admin)):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    email = require(form, "email", "Email")
    enabled = form.get("enabled") == "true"
    db.set_manual_week4_access(email, batch, enabled)
    return RedirectResponse(f"/admin/reminders?batch={batch}", status_code=303)



@app.post("/admin/reminders/send")
async def admin_reminders_send(request: Request, username: str = Depends(check_admin)):
    """
    Sends the week-4 reminder. Two modes:
      - one student:  POST with email=<address>
      - everyone due: POST with no email
    Only students whose 30 days have elapsed and who haven't submitted
    week-4 are ever mailed -- a single-student send is checked against the
    same gate as the bulk one, so a stray click can't mail someone early.
    """
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    one_email = (form.get("email") or "").strip()

    due, _waiting, _complete = _reminder_rows(batch)
    targets = [r for r in due if r["email"].lower() == one_email.lower()] if one_email else due

    sent = failed = 0
    for row in targets:
        html = templates.get_template("email_week4_reminder.html").render(
            app_name=settings.APP_NAME, name=row["name"], link=row["link"],
            status_url=eligibility.status_link(row["email"], batch),
            open_days=settings.WEEK4_OPEN_DAYS,
        )
        try:
            ok = email_utils.send_result_email(
                row["email"], row["name"],
                f"{settings.APP_NAME} — your 4-week check-in is open", html, None,
            )
        except Exception as exc:                     # SMTP down, bad address, etc.
            print(f"[reminder] failed for {row['email']}: {exc}")
            ok = False
        if ok:
            db.log_week4_reminder(row["email"], batch)
            sent += 1
        else:
            failed += 1

    return RedirectResponse(
        f"/admin/reminders?batch={batch}&sent={sent}&failed={failed}",
        status_code=303,
    )


@app.get("/admin/export.csv")
def admin_export(request: Request, username: str = Depends(check_admin)):
    import csv
    import io
    batch = batch_from(request)
    all_docs = db.all_responses(batch)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "name", "stage", "submitted_at", "job_intelligence",
                      "job_search", "quadrant", "control_mean"])
    for d in all_docs:
        s = d.get("scores", {})
        ji = s.get("job_intelligence", {}).get("total")
        js = s.get("job_search", {}).get("total") if s.get("job_search") else ""
        writer.writerow([d["email"], d["name"], d["stage"], d["submitted_at"].isoformat(),
                          ji, js, s.get("quadrant", ""), s.get("control_mean", "")])

    return HTMLResponse(content=buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{batch}-responses.csv"'
    })

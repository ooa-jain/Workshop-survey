import random
import secrets
import traceback
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, scoring, charts_svg, charts_email, email_utils, eligibility, exports, questions
from .questions import SCENARIOS_PRE_WEEK4, SCENARIOS_SAMEDAY
from .config import settings

app = FastAPI(title=settings.APP_NAME)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

security = HTTPBasic()


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


def _wants_html(request):
    """API routes and fetch() callers want JSON; a student who just pressed
    Submit on a form wants a page they can read."""
    if request.url.path.startswith("/api/"):
        return False
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return True
    return "application/json" not in accept


def _error_page(request, status_code, headline, detail, advice, kicker="Something went wrong"):
    return templates.TemplateResponse(
        request, "error.html",
        base_ctx(request, batch_from(request), headline=headline, detail=detail,
                 advice=advice, kicker=kicker),
        status_code=status_code,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """A missing answer used to come back as a wall of raw JSON. Students get
    a page that names what is missing instead; the admin's browser prompt for
    401s and every /api/ route keep their original behaviour."""
    if exc.status_code == 401 or not _wants_html(request):
        return await http_exception_handler(request, exc)

    detail = exc.detail if isinstance(exc.detail, str) else "That request could not be completed."
    if exc.status_code == 404:
        return _error_page(request, 404, "Page <em>not found</em>", detail,
                           "Check the link you followed, or start again from the survey home page.",
                           kicker="Not found")
    if str(detail).startswith("Missing required field"):
        field = str(detail).split(":", 1)[1].strip()
        return _error_page(
            request, exc.status_code, "One answer is <em>missing</em>",
            f"{field} was left blank, so the form could not be submitted.",
            "Go back, answer that question, and press Submit again. Nothing else you typed is lost.",
            kicker="Incomplete form",
        )
    return _error_page(request, exc.status_code, "That didn\u2019t go through", detail,
                       "Go back and try again.")


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Last resort. Logs the traceback for the admin and shows the student a
    plain-English page rather than a bare 'Internal Server Error'."""
    print(f"[error] {request.method} {request.url.path} failed:")
    traceback.print_exc()
    if not _wants_html(request):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return _error_page(
        request, 500, "Something broke <em>on our side</em>",
        "The server hit an unexpected error while handling that request.",
        "Please try pressing Submit once more. If it happens again, tell the workshop team \u2014 "
        "the error has been logged with the exact time.",
    )


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
    student who has just finished Pre immediately sees the Post Survey 1
    sitting open and the Post Survey 2 one counting down."""
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
        rows.append(stage_row("Post Survey 1", sameday, False))
    if week4:
        rows.append(stage_row("Post Survey 2", week4, True))

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
        quad_series.append({"label": "Post Survey 1", "job_search": None,
                            "job_intelligence": sameday["scores"]["job_intelligence"]["total"]})
    if week4:
        quad_series.append({"label": "Post Survey 2", "job_search": week4["scores"]["job_search"]["total"],
                            "job_intelligence": week4["scores"]["job_intelligence"]["total"]})

    dim_svg_rows = []
    if pre and "dimensions" in pre["scores"]["job_intelligence"]:
        for i, dim in enumerate(pre["scores"]["job_intelligence"]["dimensions"]):
            pts = [{"label": "Pre", "score_0_3": dim["score_0_3"]}]
            if sameday:
                pts.append({"label": "Post Survey 1",
                            "score_0_3": sameday["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
            if week4:
                pts.append({"label": "Post Survey 2",
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
    # to gate the Post Survey 2.
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

    email_utils.queue_send("pre", _send_pre_email, name, email, ji, js, control, quad)

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

    # The Post Survey 1 no longer asks for a password -- it's a low-stakes
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
            {"label": "Post Survey 1", "job_search": None, "job_intelligence": ji["total"]},
        ]
        for i, dim in enumerate(ji["dimensions"]):
            pts = [{"label": "Pre", "score_0_3": pre_ji["dimensions"][i]["score_0_3"]},
                   {"label": "Post Survey 1", "score_0_3": dim["score_0_3"]}]
            dim_rows_svg.append(charts_svg.dimension_arrow_row(dim["desc"], dim["left"], dim["right"], pts))
            dim_rows_png.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"], "points": pts})
    else:
        for dim in ji["dimensions"]:
            pts = [{"label": "Today", "score_0_3": dim["score_0_3"]}]
            dim_rows_svg.append(charts_svg.dimension_arrow_row(dim["desc"], dim["left"], dim["right"], pts))
            dim_rows_png.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"], "points": pts})

    email_utils.queue_send("post_sameday", _send_sameday_email,
                           name, email, ji, quad_series, dim_rows_png)

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

    quad_series = [{"label": "Post Survey 2", "job_search": js["total"], "job_intelligence": ji["total"]}]
    dim_points_by_dim = [[{"label": "Post Survey 2", "score_0_3": d["score_0_3"]}] for d in ji["dimensions"]]

    if pre_doc:
        pre_ji = pre_doc["scores"]["job_intelligence"]
        pre_js = pre_doc["scores"]["job_search"]
        quad_series = [{"label": "Pre", "job_search": pre_js["total"], "job_intelligence": pre_ji["total"]}]
        if sameday_doc:
            quad_series.append({"label": "Post Survey 1", "job_search": None,
                                 "job_intelligence": sameday_doc["scores"]["job_intelligence"]["total"]})
        quad_series.append({"label": "Post Survey 2", "job_search": js["total"], "job_intelligence": ji["total"]})

        for i, d in enumerate(ji["dimensions"]):
            pts = [{"label": "Pre", "score_0_3": pre_ji["dimensions"][i]["score_0_3"]}]
            if sameday_doc:
                pts.append({"label": "Post Survey 1",
                            "score_0_3": sameday_doc["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
            pts.append({"label": "Post Survey 2", "score_0_3": d["score_0_3"]})
            dim_points_by_dim[i] = pts

    dim_rows_svg = [
        charts_svg.dimension_arrow_row(d["desc"], d["left"], d["right"], dim_points_by_dim[i])
        for i, d in enumerate(ji["dimensions"])
    ]
    dim_rows_png = [
        {"desc": d["desc"], "left": d["left"], "right": d["right"], "points": dim_points_by_dim[i]}
        for i, d in enumerate(ji["dimensions"])
    ]

    email_utils.queue_send("post_week4", _send_week4_email,
                           name, email, ji, js, quad, quad_series, dim_rows_png, scores)

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

QUADRANTS = ["Job Intelligent", "Busy Strategist", "Volume Applicant", "Drifting"]


def _avg(vals, digits=1):
    return round(sum(vals) / len(vals), digits) if vals else None


def _direction(deltas):
    """Split a list of deltas into rose / fell / unchanged counts."""
    return {
        "rose": sum(1 for d in deltas if d > 0),
        "fell": sum(1 for d in deltas if d < 0),
        "same": sum(1 for d in deltas if d == 0),
    }


def _mean_dim_rows(matched, first_key, second_key):
    """Per-dimension mean 0-3 movement between two stage keys, one row per
    Job-Intelligence dimension, ready for the diverging-bars chart."""
    rows = []
    for i, dim in enumerate(scoring.JI_DIMENSIONS):
        deltas = [
            m[second_key]["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]
            - m[first_key]["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]
            for m in matched
        ]
        rows.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"],
                     "mean_delta": round(sum(deltas) / len(deltas), 2) if deltas else 0.0})
    return rows


def build_outcome(batch, day=None):
    """'First day' view: how the cohort moved Pre -> Post Survey 1, for every
    student who filled both. Post Survey 1 carries no Job-Search score, so this is
    a Job-Intelligence movement story -- who rose, who fell, and by how much.

    day: optional 'YYYY-MM-DD' restricting the analysis to the workshop group
    that filled its Pre survey on that day. None covers the whole batch."""
    sd = db.matched_sameday(batch, day=day)
    rows = []
    field_students = []
    quad_counts_pre = {q: 0 for q in QUADRANTS}
    quad_counts_now = {q: 0 for q in QUADRANTS}
    for m in sd:
        ji_pre = m["pre"]["scores"]["job_intelligence"]["total"]
        ji_now = m["sameday"]["scores"]["job_intelligence"]["total"]
        js_pre = m["pre"]["scores"]["job_search"]["total"]
        # Post Survey 1 asks only the Job-Intelligence items, so job search is held
        # at its Pre value -- the same assumption the quadrant field chart
        # makes. That lets the Post Survey 1 role be named on the same grid.
        q_pre = scoring.quadrant(js_pre, ji_pre)
        q_now = scoring.quadrant(js_pre, ji_now)
        quad_counts_pre[q_pre] += 1
        quad_counts_now[q_now] += 1
        rows.append({"name": m["name"], "email": m["email"],
                     "ji_pre": ji_pre, "ji_now": ji_now, "delta": round(ji_now - ji_pre, 1),
                     "js_pre": js_pre,
                     "quad_pre": q_pre, "quad_now": q_now, "moved": q_pre != q_now})
        # Post Survey 1 carries no Job-Search score, so search is unchanged from Pre:
        # each arrow is a purely vertical Job-Intelligence move.
        field_students.append({"job_search_pre": js_pre, "ji_pre": ji_pre,
                               "job_search_w4": js_pre, "ji_w4": ji_now})
    rows.sort(key=lambda r: r["delta"], reverse=True)

    deltas = [r["delta"] for r in rows]
    dirs = _direction(deltas)
    ji_pre_mean = _avg([r["ji_pre"] for r in rows])
    ji_now_mean = _avg([r["ji_now"] for r in rows])
    dim_rows = _mean_dim_rows(sd, "pre", "sameday")

    return {
        "has_data": bool(sd),
        "n": len(sd),
        "day": day,
        "n_pre": db.stage_count(batch, "pre", day=day),
        "n_sameday": db.stage_count(batch, "post_sameday", day=day),
        "quadrants": QUADRANTS,
        "quad_counts_pre": quad_counts_pre,
        "quad_counts_now": quad_counts_now,
        "n_moved_quad": sum(1 for r in rows if r["moved"]),
        "js_pre_mean": _avg([r["js_pre"] for r in rows]),
        "rose": dirs["rose"], "fell": dirs["fell"], "same": dirs["same"],
        "pct_improved": (round(dirs["rose"] / len(sd) * 100) if sd else None),
        "ji_pre_mean": ji_pre_mean,
        "ji_now_mean": ji_now_mean,
        "ji_delta": (round(ji_now_mean - ji_pre_mean, 1) if sd else None),
        "best": rows[0] if rows else None,
        "rows": rows,
        "field_svg": charts_svg.quadrant_field_svg(field_students, end_label="Post Survey 1") if sd else None,
        "bars_svg": charts_svg.diverging_bars_svg(dim_rows, span_label="Pre → Post Survey 1") if sd else None,
        "slope_svg": charts_svg.slopegraph_svg(
            [{"ji_pre": r["ji_pre"], "ji_w4": r["ji_now"]} for r in rows],
            col_labels=["Pre", "Post Survey 1"]) if sd else None,
    }


def build_impact(batch):
    """'Four weeks on' view: the full Pre -> Post Survey 2 journey for every matched
    student -- Job-Intelligence and Job-Search movement, quadrant (role)
    migrations as a from/to matrix, per-dimension change, trajectory, and the
    control-item credibility check. This is the deep-dive dashboard."""
    matched = db.matched_students(batch)

    rows = []
    ji_deltas, js_deltas = [], []
    has_sameday = False
    quad_counts_pre = {q: 0 for q in QUADRANTS}
    quad_counts_w4 = {q: 0 for q in QUADRANTS}
    trans = {a: {b: 0 for b in QUADRANTS} for a in QUADRANTS}
    field_students, slope_students = [], []

    for m in matched:
        pre_s, w4_s = m["pre"]["scores"], m["week4"]["scores"]
        ji_pre = pre_s["job_intelligence"]["total"]
        ji_w4 = w4_s["job_intelligence"]["total"]
        js_pre = pre_s["job_search"]["total"]
        js_w4 = w4_s["job_search"]["total"]
        qp, qw = pre_s["quadrant"], w4_s["quadrant"]
        ji_d, js_d = round(ji_w4 - ji_pre, 1), round(js_w4 - js_pre, 1)

        ji_deltas.append(ji_d)
        js_deltas.append(js_d)
        quad_counts_pre[qp] += 1
        quad_counts_w4[qw] += 1
        trans[qp][qw] += 1

        sd = m.get("sameday")
        ji_sd = sd["scores"]["job_intelligence"]["total"] if sd else None
        if sd:
            has_sameday = True

        field_students.append({"job_search_pre": js_pre, "ji_pre": ji_pre,
                               "job_search_w4": js_w4, "ji_w4": ji_w4})
        slope_students.append({"ji_pre": ji_pre, "ji_w4": ji_w4,
                               "ji_sameday": ji_sd if ji_sd is not None else ji_pre})
        rows.append({"name": m["name"], "email": m["email"],
                     "quad_pre": qp, "quad_w4": qw, "moved": qp != qw,
                     "ji_pre": ji_pre, "ji_w4": ji_w4, "ji_delta": ji_d,
                     "js_pre": js_pre, "js_w4": js_w4, "js_delta": js_d,
                     "ji_sameday": ji_sd})
    rows.sort(key=lambda r: r["ji_delta"], reverse=True)

    control_deltas = [
        scoring.control_shift(m["pre"]["scores"]["control_mean"], m["week4"]["scores"]["control_mean"])["delta"]
        for m in matched
    ]
    mean_control_shift = round(sum(control_deltas) / len(control_deltas), 2) if control_deltas else 0.0

    # Quadrant (role) migrations, biggest first, self-transitions excluded.
    transitions = [{"from": a, "to": b, "count": trans[a][b]}
                   for a in QUADRANTS for b in QUADRANTS if a != b and trans[a][b]]
    transitions.sort(key=lambda t: -t["count"])
    n_moved_quad = sum(1 for r in rows if r["moved"])

    return {
        "has_data": bool(matched),
        "n": len(matched),
        "n_pre": len(db.all_responses(batch, "pre")),
        "n_week4": len(db.all_responses(batch, "post_week4")),
        "has_sameday": has_sameday,
        "quadrants": QUADRANTS,
        "quad_counts_pre": quad_counts_pre,
        "quad_counts_w4": quad_counts_w4,
        "trans": trans,
        "transitions": transitions,
        "n_moved_quad": n_moved_quad,
        "n_same_quad": len(matched) - n_moved_quad,
        "ji": {**_direction(ji_deltas),
               "pre_mean": _avg([r["ji_pre"] for r in rows]),
               "w4_mean": _avg([r["ji_w4"] for r in rows]),
               "delta": (round(_avg([r["ji_w4"] for r in rows]) - _avg([r["ji_pre"] for r in rows]), 1) if rows else None)},
        "js": {**_direction(js_deltas),
               "pre_mean": _avg([r["js_pre"] for r in rows]),
               "w4_mean": _avg([r["js_w4"] for r in rows]),
               "delta": (round(_avg([r["js_w4"] for r in rows]) - _avg([r["js_pre"] for r in rows]), 1) if rows else None)},
        "mean_control_shift": mean_control_shift,
        "rows": rows,
        "field_svg": charts_svg.quadrant_field_svg(field_students),
        "bars_svg": charts_svg.diverging_bars_svg(_mean_dim_rows(matched, "pre", "week4")),
        "slope_svg": charts_svg.slopegraph_svg(slope_students, has_sameday=has_sameday),
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, username: str = Depends(check_admin)):
    batch = batch_from(request)

    pre_all = db.all_responses(batch, "pre")
    sameday_all = db.all_responses(batch, "post_sameday")
    week4_all = db.all_responses(batch, "post_week4")

    n_due = sum(1 for s in db.cohort_arcs(batch) if eligibility.is_due_for_week4_reminder(s["arc"]))
    imp = build_impact(batch)

    return templates.TemplateResponse(request, "admin_dashboard.html", base_ctx(
        request, batch,
        n_pre=len(pre_all), n_sameday=len(sameday_all), n_week4=len(week4_all),
        n_matched=imp["n"], n_due=n_due,
        quad_counts_pre=imp["quad_counts_pre"], quad_counts_w4=imp["quad_counts_w4"],
        mean_control_shift=imp["mean_control_shift"],
        field_svg=imp["field_svg"], bars_svg=imp["bars_svg"], slope_svg=imp["slope_svg"],
    ))


def _admin_n_due(batch):
    dev_mode = db.get_dev_mode()
    return sum(1 for s in db.cohort_arcs(batch)
               if eligibility.is_due_for_week4_reminder(s["arc"], dev_mode=dev_mode))


@app.get("/admin/outcome", response_class=HTMLResponse)
def admin_outcome(request: Request, username: str = Depends(check_admin)):
    """First-day tab: Pre -> Post Survey 1 movement, full analysis."""
    batch = batch_from(request)
    return templates.TemplateResponse(request, "admin_outcome.html", base_ctx(
        request, batch, o=build_outcome(batch), n_due=_admin_n_due(batch),
    ))


@app.get("/admin/impact", response_class=HTMLResponse)
def admin_impact(request: Request, username: str = Depends(check_admin)):
    """Four-weeks tab: Pre -> Post Survey 2 journey, quadrant migrations, full analysis."""
    batch = batch_from(request)
    return templates.TemplateResponse(request, "admin_impact.html", base_ctx(
        request, batch, m=build_impact(batch), n_due=_admin_n_due(batch),
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
                        pts.append({"label": "Post Survey 1", "score_0_3": sameday["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
                    if week4 and "scores" in week4 and "job_intelligence" in week4["scores"] and "dimensions" in week4["scores"]["job_intelligence"]:
                        pts.append({"label": "Post Survey 2", "score_0_3": week4["scores"]["job_intelligence"]["dimensions"][i]["score_0_3"]})
                    dim_svg_rows.append(charts_svg.dimension_arrow_row(dim["desc"], dim["left"], dim["right"], pts))

            quad_series = []
            if pre and js_pre is not None and ji_pre is not None:
                quad_series.append({"label": "Pre", "job_search": js_pre, "job_intelligence": ji_pre})
            if sameday and "scores" in sameday and "job_intelligence" in sameday["scores"]:
                quad_series.append({"label": "Post Survey 1", "job_search": None, "job_intelligence": sameday["scores"]["job_intelligence"]["total"]})
            if week4 and "scores" in week4 and "job_search" in week4["scores"] and "job_intelligence" in week4["scores"]:
                quad_series.append({"label": "Post Survey 2", "job_search": week4["scores"]["job_search"]["total"], "job_intelligence": week4["scores"]["job_intelligence"]["total"]})

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
        deleted=request.query_params.get("deleted"),
    ))


@app.post("/admin/student/delete")
async def admin_student_delete(request: Request, username: str = Depends(check_admin)):
    """Delete a single student's data (all stages) from this batch."""
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    email = require(form, "email", "Email")
    deleted = db.delete_student(email, batch)
    return RedirectResponse(f"/admin/student?batch={batch}&deleted={deleted}", status_code=303)


# ---------------------------------------------------------------------------
# ADMIN -- GROUPS (by fill date) + time-to-fill
# ---------------------------------------------------------------------------

STAGE_LABEL = {"pre": "Pre", "post_sameday": "Post Survey 1", "post_week4": "Post Survey 2"}


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
                "stage_key": d["stage"],
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
# ADMIN -- ALL DATA
#
# Everything every student typed, question by question. The analysis tabs
# answer "what did this cohort do"; this one answers "what exactly did this
# person put", which is the question you have when someone queries their
# result or you are checking a number by hand.
# ---------------------------------------------------------------------------

DATA_PAGE_SIZE = 100


@app.get("/admin/data", response_class=HTMLResponse)
def admin_data(request: Request, username: str = Depends(check_admin)):
    """Every submission in the batch, newest first, searchable by name or
    email. One row per submission, not per student -- a student who did all
    three surveys is three rows."""
    batch = batch_from(request)
    query = (request.query_params.get("q") or "").strip()
    stage_filter = (request.query_params.get("stage") or "").strip()

    docs = db.all_responses(batch)
    if stage_filter:
        docs = [d for d in docs if d["stage"] == stage_filter]
    if query:
        needle = query.lower()
        docs = [d for d in docs
                if needle in d.get("name", "").lower() or needle in d.get("email", "").lower()]
    docs.sort(key=lambda d: d["submitted_at"], reverse=True)

    total = len(docs)
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    pages = max(1, (total + DATA_PAGE_SIZE - 1) // DATA_PAGE_SIZE)
    page = min(page, pages)
    window = docs[(page - 1) * DATA_PAGE_SIZE: page * DATA_PAGE_SIZE]

    rows = []
    for d in window:
        sc = d.get("scores", {})
        rows.append({
            "name": d.get("name", ""), "email": d.get("email", ""),
            "stage": d["stage"], "stage_label": STAGE_LABEL.get(d["stage"], d["stage"]),
            "at": d["submitted_at"].strftime("%d %b %Y, %H:%M"),
            "time": fmt_duration(fill_seconds(d)),
            "ji": (sc.get("job_intelligence") or {}).get("total"),
            "js": (sc.get("job_search") or {}).get("total"),
            "quadrant": sc.get("quadrant"),
            "n_answered": sum(1 for a in questions.answers_for(d) if a["answered"]),
            "n_questions": len(questions.QUESTIONS.get(d["stage"], [])),
            "resubmissions": max(0, (d.get("submission_count") or 1) - 1),
        })

    return templates.TemplateResponse(request, "admin_data.html", base_ctx(
        request, batch, rows=rows, q=query, stage_filter=stage_filter,
        total=total, page=page, pages=pages, page_size=DATA_PAGE_SIZE,
        stage_labels=STAGE_LABEL, n_due=_admin_n_due(batch),
    ))


@app.get("/admin/data/response", response_class=HTMLResponse)
def admin_data_response(request: Request, username: str = Depends(check_admin)):
    """One submission in full: every question on that survey with what this
    student answered, in the order they were asked."""
    batch = batch_from(request)
    email = (request.query_params.get("email") or "").strip()
    stage = (request.query_params.get("stage") or "").strip()

    if stage not in STAGE_LABEL:
        raise HTTPException(status_code=404, detail="Unknown survey stage.")
    doc = db.get_response(email, stage, batch) if email else None
    if not doc:
        raise HTTPException(status_code=404, detail="No such submission in this batch.")

    arc = db.get_student_arc(email, batch)
    return templates.TemplateResponse(request, "admin_response.html", base_ctx(
        request, batch, doc=doc, email=email, stage=stage,
        stage_label=STAGE_LABEL[stage],
        answers=questions.answers_for(doc),
        extras=questions.extra_fields(doc),
        submitted_at=doc["submitted_at"].strftime("%d %b %Y, %H:%M"),
        first_submitted_at=(doc.get("first_submitted_at").strftime("%d %b %Y, %H:%M")
                            if doc.get("first_submitted_at") else None),
        resubmissions=max(0, (doc.get("submission_count") or 1) - 1),
        fill_time=fmt_duration(fill_seconds(doc)),
        other_stages=[(st, STAGE_LABEL[st]) for st in ("pre", "post_sameday", "post_week4")
                      if st in arc and st != stage],
        n_due=_admin_n_due(batch),
    ))


@app.get("/admin/data/answers.csv")
def admin_answers_csv(request: Request, username: str = Depends(check_admin)):
    """Every answer to every question, one row per submission.

    The Download CSV on the other tabs carries the computed scores; this one
    carries what the students actually typed, with the questions as column
    headers, for anyone who wants to do their own analysis in a spreadsheet.
    """
    import csv
    import io

    batch = batch_from(request)
    stage = (request.query_params.get("stage") or "").strip()
    if stage and stage not in STAGE_LABEL:
        raise HTTPException(status_code=404, detail="Unknown survey stage.")

    stages = [stage] if stage else ["pre", "post_sameday", "post_week4"]
    buf = io.StringIO()
    writer = csv.writer(buf)

    for st in stages:
        docs = [d for d in db.all_responses(batch, st)]
        docs.sort(key=lambda d: d["submitted_at"], reverse=True)
        qs = questions.QUESTIONS.get(st, [])

        writer.writerow([STAGE_LABEL[st]])
        writer.writerow(["name", "email", "submitted_at", "seconds_to_fill", "times_filled"]
                        + [f"{q['num']} {q['text']}" for q in qs])
        for d in docs:
            answers = {a["num"]: a for a in questions.answers_for(d)}
            writer.writerow([
                d.get("name", ""), d.get("email", ""),
                d["submitted_at"].strftime("%Y-%m-%d %H:%M:%S"),
                fill_seconds(d) if fill_seconds(d) is not None else "",
                d.get("submission_count") or 1,
            ] + [(answers.get(q["num"], {}).get("answer") or "") for q in qs])
        writer.writerow([])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="answers-{batch}.csv"'},
    )


# ---------------------------------------------------------------------------
# ADMIN -- DUPLICATES
#
# Two different things get called a duplicate, and they need separating.
#
# One person filling the same survey twice under the same address is not a
# duplicate in the data: the unique index means the second sitting overwrote
# the first, so the recent one is already the only one. It is still worth
# seeing that it happened, which is what submission_count records.
#
# One person entering under two different addresses IS a duplicate, and no
# index can catch it -- to Mongo those are two students. That inflates every
# headcount until somebody resolves it, which is what this page is for.
# ---------------------------------------------------------------------------

@app.get("/admin/duplicates", response_class=HTMLResponse)
def admin_duplicates(request: Request, username: str = Depends(check_admin)):
    batch = batch_from(request)
    clusters = db.duplicate_students(batch)

    view = []
    for c in clusters:
        entries = []
        for i, e in enumerate(c["entries"]):
            entries.append({
                "name": e["name"], "email": e["email"],
                "stages": [STAGE_LABEL[st] for st in ("pre", "post_sameday", "post_week4")
                           if st in e["stages"]],
                "n_stages": len(e["stages"]),
                "last_at": e["last_at"].strftime("%d %b %Y, %H:%M"),
                "keep": i == 0,                      # newest first, so [0] is the survivor
            })
        view.append({
            "reason": c["reason"], "key": c["key"], "entries": entries,
            "keep_email": c["entries"][0]["email"],
            "drop_emails": [e["email"] for e in c["entries"][1:]],
            "n_drop_responses": sum(len(e["stages"]) for e in c["entries"][1:]),
        })

    resubmitted = db.resubmitted_students(batch)
    for r in resubmitted:
        r["stage"] = STAGE_LABEL.get(r["stage"], r["stage"])

    return templates.TemplateResponse(request, "admin_duplicates.html", base_ctx(
        request, batch, clusters=view, resubmitted=resubmitted,
        n_due=_admin_n_due(batch),
        resolved=request.query_params.get("resolved"),
    ))


@app.post("/admin/duplicates/resolve")
async def admin_duplicates_resolve(request: Request, username: str = Depends(check_admin)):
    """Keep the most recent entry in one cluster and delete the older ones.

    Only ever deletes the addresses named in the form, so what gets removed
    is exactly what the page listed under them -- a cluster that changed
    since the page was rendered cannot take an unlisted student with it.
    """
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    keep = require(form, "keep", "Address to keep")
    drop = [e for e in form.getlist("drop") if e.strip() and e.strip().lower() != keep.strip().lower()]

    deleted = 0
    for email in drop:
        deleted += db.delete_student(email, batch)
    return RedirectResponse(f"/admin/duplicates?batch={batch}&resolved={deleted}", status_code=303)


# ---------------------------------------------------------------------------
# ADMIN -- SHARE AN ANALYSIS
#
# A share is a read-only public window onto one group's first-day results:
# where the group started (Pre), where it finished by the end of the workshop
# day (Post Survey 1), and every arrow in between. It carries a title the admin
# writes, and it never exposes email addresses -- names are optional too.
# ---------------------------------------------------------------------------

def _share_url(token):
    return f"{settings.BASE_URL.rstrip('/')}/s/{token}"


def _group_label(day):
    """Human name for the group a share covers."""
    if not day:
        return "All groups"
    return datetime.strptime(day, "%Y-%m-%d").strftime("%d %b %Y")


def _share_view(share):
    """The analysis behind one share link, plus its per-student rows with
    email addresses stripped and names replaced by Student 01, 02... unless
    the admin ticked 'show student names'. Rows arrive sorted biggest gain
    first, so the numbering reads as a leaderboard. Both the page and the
    spreadsheet are built from this, so they can never disagree."""
    o = build_outcome(share["batch"], day=share.get("day"))
    show_names = share.get("show_names", True)
    people = [
        {
            "who": r["name"] if show_names else f"Student {i:02d}",
            "ji_pre": r["ji_pre"], "ji_now": r["ji_now"], "delta": r["delta"],
            "quad_pre": r["quad_pre"], "quad_now": r["quad_now"], "moved": r["moved"],
        }
        for i, r in enumerate(o["rows"], start=1)
    ]
    return o, people


def _decorate_share(s):
    return {
        "token": s["token"],
        "title": s["title"],
        "note": s.get("note", ""),
        "day": s.get("day"),
        "group_label": _group_label(s.get("day")),
        "show_names": s.get("show_names", True),
        "views": s.get("views", 0),
        "url": _share_url(s["token"]),
        "created": s["created_at"].strftime("%d %b %Y, %H:%M"),
    }


@app.get("/admin/share", response_class=HTMLResponse)
def admin_share(request: Request, username: str = Depends(check_admin)):
    """Create and manage public links to a group's first-day analysis."""
    batch = batch_from(request)
    return templates.TemplateResponse(request, "admin_share.html", base_ctx(
        request, batch,
        groups=db.pre_groups(batch),
        shares=[_decorate_share(s) for s in db.list_shares(batch)],
        new=request.query_params.get("new"),
        revoked=request.query_params.get("revoked"),
        n_due=_admin_n_due(batch),
    ))


@app.post("/admin/share/create")
async def admin_share_create(request: Request, username: str = Depends(check_admin)):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    title = require(form, "title", "Title")
    day = form.get("day") or None          # empty select value = whole batch
    token = secrets.token_urlsafe(9)
    db.create_share(token, batch, day, title,
                    note=form.get("note", ""),
                    show_names=form.get("show_names") == "on")
    return RedirectResponse(f"/admin/share?batch={batch}&new={token}", status_code=303)


@app.post("/admin/share/delete")
async def admin_share_delete(request: Request, username: str = Depends(check_admin)):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    token = require(form, "token", "Share token")
    db.delete_share(token)
    return RedirectResponse(f"/admin/share?batch={batch}&revoked=1", status_code=303)


@app.get("/s/{token}", response_class=HTMLResponse)
def shared_analysis(request: Request, token: str):
    """The public page. No login: anyone holding the link sees this group's
    results, and nothing else -- no other group, no admin controls, no email
    addresses."""
    share = db.get_share(token)
    if not share:
        return templates.TemplateResponse(
            request, "share_missing.html",
            base_ctx(request, settings.WORKSHOP_BATCH), status_code=404,
        )
    db.record_share_view(token)
    o, people = _share_view(share)

    return templates.TemplateResponse(request, "shared_analysis.html", base_ctx(
        request, share["batch"], o=o, people=people, token=token,
        share_title=share["title"], share_note=share.get("note", ""),
        group_label=_group_label(share.get("day")),
    ))


@app.get("/s/{token}/students.xlsx")
def shared_analysis_xlsx(token: str):
    """The per-student table as a spreadsheet. Deliberately built from the same
    anonymised rows the page renders, so the download can never reveal a name
    the page itself hides, and carries no email addresses either way."""
    share = db.get_share(token)
    if not share:
        raise HTTPException(status_code=404, detail="This shared analysis link is not valid.")

    o, people = _share_view(share)
    data = exports.shared_analysis_xlsx(
        share["title"], _group_label(share.get("day")), o, people)
    filename = exports.safe_filename(share["title"])
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# ADMIN -- POST SURVEY 2 REMINDERS
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
        queued=request.query_params.get("queued"),
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
    Sends the Post Survey 2 reminder. Two modes:
      - one student:  POST with email=<address>
      - everyone due: POST with no email
    Only students whose 30 days have elapsed and who haven't submitted
    Post Survey 2 are ever mailed -- a single-student send is checked against the
    same gate as the bulk one, so a stray click can't mail someone early.
    """
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)
    one_email = (form.get("email") or "").strip()

    due, _waiting, _complete = _reminder_rows(batch)
    targets = [r for r in due if r["email"].lower() == one_email.lower()] if one_email else due

    # The mail itself goes out in the background, down a single SMTP
    # connection. A cohort of 500 sent inline would take the request far past
    # gunicorn's timeout and leave the admin looking at a dead page with no
    # way to tell how far it got. Instead the page comes straight back and
    # the table fills in as the run progresses -- every successful send is
    # recorded on that student's Pre document the moment it lands, so a
    # refresh is an accurate picture of where the run has reached.
    subject = f"{settings.APP_NAME} \u2014 your Post Survey 2 is open"
    items = []
    for row in targets:
        html = templates.get_template("email_week4_reminder.html").render(
            app_name=settings.APP_NAME, name=row["name"], link=row["link"],
            status_url=eligibility.status_link(row["email"], batch),
            open_days=settings.WEEK4_OPEN_DAYS,
        )
        items.append((row["email"], row["name"], subject, html))

    if items:
        email_utils.queue_send(
            "week4-reminders", email_utils.send_many, "week4-reminder", items,
            on_sent=lambda addr: db.log_week4_reminder(addr, batch),
        )

    return RedirectResponse(
        f"/admin/reminders?batch={batch}&queued={len(items)}",
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

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
    return JSONResponse({
        "ok": st["state"] in ("open", "done"),
        "state": st["state"],
        "headline": st["headline"],
        "detail": st["detail"],
        "name": name,
        "status_url": f"/status?batch={batch}&email={email}",
    })



# ---------------------------------------------------------------------------
# PRE
# ---------------------------------------------------------------------------

@app.get("/survey/pre", response_class=HTMLResponse)
def pre_form(request: Request):
    batch = batch_from(request)
    return templates.TemplateResponse(request, "pre.html", base_ctx(
        request, batch,
        prefill_email=(request.query_params.get("email") or "").strip(),
        prefill_name="",
    ))


@app.post("/survey/pre", response_class=HTMLResponse)
async def pre_submit(request: Request):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)

    name = require(form, "name", "Name")
    email = require(form, "email", "Email")

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
    db.upsert_response(email, name, "pre", batch, raw, scores)

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
    return templates.TemplateResponse(request, "post_sameday.html", base_ctx(
        request, batch, prefill_email=email, prefill_name="",
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

    b_answers = [require(form, f"b{i}", f"Scenario B{i}") for i in range(1, 6)]
    ji = scoring.score_job_intelligence(b_answers)

    c1, c2, c3 = require(form, "c1"), require(form, "c2"), require(form, "c3")
    control = scoring.control_mean(c1, c2, c3)

    raw = {k: v for k, v in form.multi_items()}
    pre_doc = db.get_response(email, "pre", batch)

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

    return templates.TemplateResponse(request, "post_week4.html", base_ctx(
        request, batch, prefill_email=email, prefill_name=prefill_name,
    ))


@app.post("/survey/post-week4", response_class=HTMLResponse)
async def week4_submit(request: Request):
    form = await request.form()
    batch = form.get("batch", settings.WORKSHOP_BATCH)

    name = require(form, "name", "Name")
    email = require(form, "email", "Email")

    blocked = gate_or_none("post_week4", email, batch)
    if blocked:
        return locked_page(request, "post_week4", blocked[0], batch, email)

    b_answers = [require(form, f"b{i}", f"Scenario B{i}") for i in range(1, 6)]
    ji = scoring.score_job_intelligence(b_answers)

    a2, a3, a4 = require(form, "a2"), require(form, "a3"), require(form, "a4")
    a5 = require(form, "a5", "A5")
    js = scoring.score_job_search(a2, a3, a4, a5)

    c1, c2, c3 = require(form, "c1"), require(form, "c2"), require(form, "c3")
    control = scoring.control_mean(c1, c2, c3)

    quad = scoring.quadrant(js["total"], ji["total"])

    raw = {k: v for k, v in form.multi_items()}
    pre_doc = db.get_response(email, "pre", batch)
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

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, username: str = Depends(check_admin)):
    batch = batch_from(request)

    pre_all = db.all_responses(batch, "pre")
    sameday_all = db.all_responses(batch, "post_sameday")
    week4_all = db.all_responses(batch, "post_week4")
    matched = db.matched_students(batch)

    n_due = sum(1 for s in db.cohort_arcs(batch) if eligibility.is_due_for_week4_reminder(s["arc"]))

    students_for_charts = []
    for m in matched:
        pre_s = m["pre"]["scores"]
        w4_s = m["week4"]["scores"]
        students_for_charts.append({
            "job_search_pre": pre_s["job_search"]["total"],
            "ji_pre": pre_s["job_intelligence"]["total"],
            "job_search_w4": w4_s["job_search"]["total"],
            "ji_w4": w4_s["job_intelligence"]["total"],
        })

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
        mean_delta = round(sum(deltas) / len(deltas), 2) if deltas else 0.0
        dim_rows.append({"desc": dim["desc"], "left": dim["left"], "right": dim["right"], "mean_delta": mean_delta})

    slope_students = [{"ji_pre": m["pre"]["scores"]["job_intelligence"]["total"],
                        "ji_w4": m["week4"]["scores"]["job_intelligence"]["total"]} for m in matched]

    control_deltas = [
        scoring.control_shift(m["pre"]["scores"]["control_mean"], m["week4"]["scores"]["control_mean"])["delta"]
        for m in matched
    ]
    mean_control_shift = round(sum(control_deltas) / len(control_deltas), 2) if control_deltas else 0.0

    return templates.TemplateResponse(request, "admin_dashboard.html", base_ctx(
        request, batch,
        n_pre=len(pre_all), n_sameday=len(sameday_all), n_week4=len(week4_all),
        n_matched=len(matched), n_due=n_due,
        quad_counts_pre=quad_counts_pre, quad_counts_w4=quad_counts_w4,
        mean_control_shift=mean_control_shift,
        field_svg=charts_svg.quadrant_field_svg(students_for_charts),
        bars_svg=charts_svg.diverging_bars_svg(dim_rows),
        slope_svg=charts_svg.slopegraph_svg(slope_students, has_sameday=False),
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
            }

    return templates.TemplateResponse(request, "admin_student.html", base_ctx(
        request, batch,
        students=students_list,
        selected_email=selected_email,
        student=student_data,
        n_due=n_due,
    ))



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

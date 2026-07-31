"""
Inline SVG builders, used by the Jinja templates for on-screen result
pages and the admin dashboard. Kept as plain string-building (no JS,
no client-side computation) so every number the student or admin sees
was computed once, server-side, from the DB.

Palette matches the workshop deck: teal #005b4f, lime #a4c93a, ink #16242c.
"""

INK = "#17131F"
TEAL = "#5B3DF5"
LIME = "#D6F53F"
MUTED = "#6B6377"
RULE = "#D8CEBC"
BACK = "#F7F3EA"
WARN = "#C3312B"

FONT = "font-family:'IBM Plex Mono',monospace"


def _esc(text):
    """Escape text for safe insertion into SVG/XML content. Every label,
    description, and name that gets f-string-interpolated into an <svg>
    string in this file must go through this first -- plain text like
    'Coordination & risk' will otherwise break XML well-formedness."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def dimension_arrow_row(desc, left, right, points, width=560, row_h=54):
    """
    One dimension, rendered as a labelled track with an arrow moving from
    the first point to the last point (0-3 scale mapped across the width).
    points: ordered list of {"label": "Pre", "score_0_3": 0-3} -- 2 or 3 entries.
    Replaces the earlier stacked-bar version: this reads as movement along
    a spectrum, not as three independent measurements.
    """
    track_x0, track_x1 = 130, width - 20
    track_w = track_x1 - track_x0
    track_y = 30

    def x_of(score):
        return track_x0 + (score / 3) * track_w

    first, last = points[0], points[-1]
    delta = last["score_0_3"] - first["score_0_3"]
    arrow_colour = TEAL if delta > 0 else (WARN if delta < 0 else MUTED)
    first_label, first_score = _esc(first["label"]), first["score_0_3"]
    last_label, last_score = _esc(last["label"]), last["score_0_3"]
    desc_e = _esc(desc)

    svg = [f'<svg viewBox="0 0 {width} {row_h+34}" class="dim-row" role="img" '
           f'aria-label="{desc_e}: moved from {first_label} {first_score} '
           f'to {last_label} {last_score} on a 0 to 3 scale">']

    # end labels
    svg.append(f'<text x="{track_x0}" y="{track_y-10}" text-anchor="start" '
                f'style="{FONT};font-size:10.5px;letter-spacing:.06em" fill="{MUTED}">{_esc(left.upper())}</text>')
    svg.append(f'<text x="{track_x1}" y="{track_y-10}" text-anchor="end" '
                f'style="{FONT};font-size:10.5px;letter-spacing:.06em" fill="{TEAL}">{_esc(right.upper())}</text>')
    # dimension name, left margin
    svg.append(f'<text x="0" y="{track_y+4}" text-anchor="start" '
                f'style="font-family:Archivo,sans-serif;font-size:13px;font-weight:700" fill="{INK}">{desc_e}</text>')

    # track
    svg.append(f'<line x1="{track_x0}" y1="{track_y}" x2="{track_x1}" y2="{track_y}" '
                f'stroke="{RULE}" stroke-width="3" stroke-linecap="round"/>')
    # quartile ticks
    for frac in (0, 1/3, 2/3, 1):
        tx = track_x0 + frac * track_w
        svg.append(f'<line x1="{tx}" y1="{track_y-4}" x2="{tx}" y2="{track_y+4}" stroke="{RULE}" stroke-width="1.5"/>')

    # intermediate point (e.g. same-day), if present
    if len(points) == 3:
        mx = x_of(points[1]["score_0_3"])
        svg.append(f'<circle cx="{mx}" cy="{track_y}" r="4.5" fill="{LIME}" stroke="{INK}" stroke-width="1.5"/>')
        svg.append(f'<text x="{mx}" y="{track_y+22}" text-anchor="middle" '
                    f'style="{FONT};font-size:9.5px" fill="{MUTED}">{_esc(points[1]["label"])}</text>')

    # start point
    fx = x_of(first["score_0_3"])
    svg.append(f'<circle cx="{fx}" cy="{track_y}" r="6" fill="{INK}"/>')
    svg.append(f'<text x="{fx}" y="{track_y-16}" text-anchor="middle" '
                f'style="{FONT};font-size:9.5px" fill="{MUTED}">{first_label}</text>')

    # arrow shaft to end point
    lx = x_of(last["score_0_3"])
    marker_id = f"arw{abs(hash((desc, fx, lx))) % 100000}"
    svg.append(f'''<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="8" refY="5"
        markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="{arrow_colour}"/></marker></defs>''')
    if abs(lx - fx) > 1:
        svg.append(f'<line x1="{fx}" y1="{track_y}" x2="{lx}" y2="{track_y}" '
                    f'stroke="{arrow_colour}" stroke-width="3.5" marker-end="url(#{marker_id})"/>')
    svg.append(f'<circle cx="{lx}" cy="{track_y}" r="7" fill="{LIME}" stroke="{INK}" stroke-width="2"/>')
    svg.append(f'<text x="{lx}" y="{track_y-16}" text-anchor="middle" '
                f'style="{FONT};font-size:9.5px;font-weight:600" fill="{TEAL}">{last_label}</text>')

    # delta chip, right edge
    sign = "+" if delta > 0 else ("" if delta == 0 else "\u2212")
    svg.append(f'<text x="{width}" y="{track_y+4}" text-anchor="end" '
                f'style="{FONT};font-size:12px;font-weight:600" fill="{arrow_colour}">{sign}{abs(delta):.0f}</text>')

    svg.append("</svg>")
    return "".join(svg)


def quadrant_svg(series, width=640, height=460):
    """
    series: ordered list of {"label", "job_search", "job_intelligence"} --
    2 points (pre, week4) or 3 (pre, sameday [JI only], week4).
    A same-day point with job_search=None is drawn as a dotted vertical
    step directly above the pre point, since search behaviour cannot have
    changed by the same afternoon.
    """
    x0, y0, pw, ph = 70, 30, 520, 360

    def X(v): return x0 + (v / 100) * pw
    def Y(v): return y0 + ph - (v / 100) * ph

    parts = [f'<svg viewBox="0 0 {width} {height}" class="quad-svg" role="img" '
             f'aria-label="Job Search versus Job Intelligence quadrant, student trajectory">']
    parts.append(f'<rect x="{x0}" y="{y0}" width="{pw}" height="{ph}" fill="{BACK}" stroke="{RULE}"/>')
    parts.append(f'<rect x="{x0}" y="{y0}" width="{pw/2}" height="{ph/2}" fill="{LIME}" opacity=".17"/>')
    parts.append(f'<line x1="{x0+pw/2}" y1="{y0}" x2="{x0+pw/2}" y2="{y0+ph}" stroke="{RULE}"/>')
    parts.append(f'<line x1="{x0}" y1="{y0+ph/2}" x2="{x0+pw}" y2="{y0+ph/2}" stroke="{RULE}"/>')
    parts.append(f'<text x="{x0+16}" y="{y0+22}" style="{FONT};font-size:11px;font-weight:600" fill="#4A6B00">JOB INTELLIGENT</text>')
    parts.append(f'<text x="{x0+pw/2+16}" y="{y0+22}" style="{FONT};font-size:11px;font-weight:600" fill="{INK}">BUSY STRATEGIST</text>')
    parts.append(f'<text x="{x0+16}" y="{y0+ph-10}" style="{FONT};font-size:11px;font-weight:600" fill="{INK}">DRIFTING</text>')
    parts.append(f'<text x="{x0+pw/2+16}" y="{y0+ph-10}" style="{FONT};font-size:11px;font-weight:600" fill="{INK}">VOLUME APPLICANT</text>')
    parts.append(f'<text x="{x0}" y="{y0+ph+44}" style="{FONT};font-size:10px;letter-spacing:.1em;text-transform:uppercase" fill="{MUTED}">Job Search (untargeted) \u2192</text>')
    parts.append(f'<text transform="rotate(-90 26 {y0+ph/2})" x="26" y="{y0+ph/2}" style="{FONT};font-size:10px;letter-spacing:.1em;text-transform:uppercase" fill="{MUTED}">Job Intelligence \u2192</text>')

    known = [p for p in series if p.get("job_search") is not None]
    marker_id = "ahq"
    parts.append(f'''<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="8" refY="5"
        markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="{TEAL}"/></marker></defs>''')

    # dotted same-day step (JI known, search unknown) directly above pre
    sameday = next((p for p in series if p.get("job_search") is None), None)
    pre = known[0] if known else None
    if sameday and pre:
        sx = X(pre["job_search"])
        parts.append(f'<line x1="{sx}" y1="{Y(pre["job_intelligence"])}" x2="{sx}" y2="{Y(sameday["job_intelligence"])}" '
                      f'stroke="{TEAL}" stroke-width="2" stroke-dasharray="4 5" opacity=".65"/>')
        parts.append(f'<circle cx="{sx}" cy="{Y(sameday["job_intelligence"])}" r="5" fill="none" stroke="{TEAL}" stroke-width="2"/>')
        parts.append(f'<text x="{sx+10}" y="{Y(sameday["job_intelligence"])-6}" style="{FONT};font-size:10px" fill="{TEAL}">{_esc(sameday["label"])} \u00b7 JI {sameday["job_intelligence"]:.0f}</text>')

    for i in range(len(known) - 1):
        a, b = known[i], known[i + 1]
        parts.append(f'<line x1="{X(a["job_search"])}" y1="{Y(a["job_intelligence"])}" '
                      f'x2="{X(b["job_search"])}" y2="{Y(b["job_intelligence"])}" '
                      f'stroke="{TEAL}" stroke-width="3.5" marker-end="url(#{marker_id})"/>')

    for i, p in enumerate(known):
        is_last = i == len(known) - 1
        fill = LIME if is_last else INK
        r = 8.5 if is_last else 7
        parts.append(f'<circle cx="{X(p["job_search"])}" cy="{Y(p["job_intelligence"])}" r="{r}" '
                      f'fill="{fill}" stroke="{INK if is_last else "none"}" stroke-width="2"/>')
        parts.append(f'<text x="{X(p["job_search"])+12}" y="{Y(p["job_intelligence"])+ (18 if is_last else -12)}" '
                      f'style="{FONT};font-size:10.5px" fill="{MUTED}">{_esc(p["label"])}</text>')

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Cohort-level charts (admin dashboard) -- same palette, driven by real
# aggregated data computed in main.py, not synthetic/random data.
# ---------------------------------------------------------------------------

def quadrant_field_svg(students, width=640, height=470):
    """
    students: list of {"job_search_pre","ji_pre","job_search_w4","ji_w4"}
    One faint arrow per student, a heavier arrow for the cohort mean.
    Arrows where JI fell are drawn in the warning colour.
    """
    x0, y0, pw, ph = 70, 30, 520, 360

    def X(v): return x0 + (v / 100) * pw
    def Y(v): return y0 + ph - (v / 100) * ph

    parts = [f'<svg viewBox="0 0 {width} {height}" class="quad-svg" role="img" '
             f'aria-label="Cohort quadrant field, one arrow per matched student">']
    parts.append(f'<rect x="{x0}" y="{y0}" width="{pw}" height="{ph}" fill="{BACK}" stroke="{RULE}"/>')
    parts.append(f'<rect x="{x0}" y="{y0}" width="{pw/2}" height="{ph/2}" fill="{LIME}" opacity=".17"/>')
    parts.append(f'<line x1="{x0+pw/2}" y1="{y0}" x2="{x0+pw/2}" y2="{y0+ph}" stroke="{RULE}"/>')
    parts.append(f'<line x1="{x0}" y1="{y0+ph/2}" x2="{x0+pw}" y2="{y0+ph/2}" stroke="{RULE}"/>')
    parts.append(f'<text x="{x0+16}" y="{y0+22}" style="{FONT};font-size:11px;font-weight:600" fill="#4A6B00">JOB INTELLIGENT</text>')
    parts.append(f'<text x="{x0+pw/2+16}" y="{y0+22}" style="{FONT};font-size:11px;font-weight:600" fill="{INK}">BUSY STRATEGIST</text>')
    parts.append(f'<text x="{x0+16}" y="{y0+ph-10}" style="{FONT};font-size:11px;font-weight:600" fill="{INK}">DRIFTING</text>')
    parts.append(f'<text x="{x0+pw/2+16}" y="{y0+ph-10}" style="{FONT};font-size:11px;font-weight:600" fill="{INK}">VOLUME APPLICANT</text>')
    parts.append(f'<text x="{x0}" y="{y0+ph+44}" style="{FONT};font-size:10px;letter-spacing:.1em;text-transform:uppercase" fill="{MUTED}">Job Search (untargeted) \u2192</text>')

    parts.append(f'''<defs>
        <marker id="ahf" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="{INK}" opacity=".5"/></marker>
        <marker id="ahm" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="{TEAL}"/></marker>
    </defs>''')

    mx = my = mx2 = my2 = 0
    n = max(len(students), 1)
    backwards = 0
    for s in students:
        wrong = s["ji_w4"] < s["ji_pre"]
        backwards += 1 if wrong else 0
        colour = WARN if wrong else INK
        parts.append(f'<line x1="{X(s["job_search_pre"])}" y1="{Y(s["ji_pre"])}" '
                      f'x2="{X(s["job_search_w4"])}" y2="{Y(s["ji_w4"])}" '
                      f'stroke="{colour}" stroke-width="1.4" opacity="{0.55 if wrong else 0.32}" '
                      f'marker-end="url(#ahf)"/>')
        mx += s["job_search_pre"]; my += s["ji_pre"]
        mx2 += s["job_search_w4"]; my2 += s["ji_w4"]

    if students:
        mx /= n; my /= n; mx2 /= n; my2 /= n
        parts.append(f'<line x1="{X(mx)}" y1="{Y(my)}" x2="{X(mx2)}" y2="{Y(my2)}" '
                      f'stroke="{TEAL}" stroke-width="5" marker-end="url(#ahm)"/>')
        parts.append(f'<circle cx="{X(mx)}" cy="{Y(my)}" r="7" fill="{INK}"/>')
        parts.append(f'<circle cx="{X(mx2)}" cy="{Y(my2)}" r="8.5" fill="{LIME}" stroke="{INK}" stroke-width="2"/>')
        parts.append(f'<text x="{X(mx)+10}" y="{Y(my)+16}" style="{FONT};font-size:10px" fill="{MUTED}">cohort mean, pre</text>')
        parts.append(f'<text x="{X(mx2)-30}" y="{Y(my2)-14}" style="{FONT};font-size:10px" fill="{MUTED}">week 4</text>')

    parts.append(f'<text x="{x0}" y="{height-8}" style="{FONT};font-size:10px" fill="{WARN}">'
                  f'red = moved backwards on Job Intelligence ({backwards} of {len(students)})</text>')
    parts.append("</svg>")
    return "".join(parts)


def diverging_bars_svg(dim_rows, width=640, height=None):
    """dim_rows: list of {"desc","left","right","mean_delta"} (0-3 scale), any order -- sorted here descending."""
    rows = sorted(dim_rows, key=lambda r: -r["mean_delta"])
    row_h = 46
    height = height or (30 + row_h * len(rows) + 40)
    x0 = 250
    max_delta = max((r["mean_delta"] for r in rows), default=1) or 1
    scale = (width - x0 - 60) / max(max_delta, 1.5)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Mean change per dimension">']
    parts.append(f'<line x1="{x0}" y1="14" x2="{x0}" y2="{height-30}" stroke="{INK}" stroke-width="1.5"/>')
    parts.append(f'<text x="{x0-4}" y="{height-10}" text-anchor="start" style="{FONT};font-size:10px" fill="{MUTED}">0</text>')
    parts.append(f'<text x="{x0}" y="{height-10}" text-anchor="start" style="{FONT};font-size:10px" fill="{MUTED}">'
                  f'Mean change per dimension, pre \u2192 week 4 (0\u20133 scale)</text>')

    for i, r in enumerate(rows):
        y = 24 + i * row_h
        w = max(r["mean_delta"], 0) * scale
        strong = r["mean_delta"] >= 0.9
        colour = TEAL if strong else "#0E8F96"
        parts.append(f'<text x="{x0-14}" y="{y+11}" text-anchor="end" '
                      f'style="font-family:Archivo,sans-serif;font-size:12px;font-weight:600" fill="{INK}">{_esc(r["desc"])}</text>')
        parts.append(f'<text x="{x0-14}" y="{y+25}" text-anchor="end" style="{FONT};font-size:10px" fill="{MUTED}">'
                      f'{_esc(r["left"])} \u2192 {_esc(r["right"])}</text>')
        parts.append(f'<rect x="{x0}" y="{y}" width="{w}" height="22" fill="{colour}"/>')
        parts.append(f'<text x="{x0+w+9}" y="{y+16}" style="{FONT};font-size:12.5px;font-weight:600" fill="{colour}">'
                      f'+{r["mean_delta"]:.2f}</text>')
    parts.append("</svg>")
    return "".join(parts)


def slopegraph_svg(students, width=640, height=380, has_sameday=False):
    """
    students: list of {"label"(unused), "ji_pre", "ji_sameday"(optional), "ji_w4"}
    Renders individual thin lines plus a heavy cohort-mean line across
    2 or 3 columns depending on whether same-day data is available for
    (at least some of) the cohort.
    """
    labels = ["Pre", "Week 4"] if not has_sameday else ["Pre", "Same day", "Week 4"]
    n_cols = len(labels)
    x0, x1 = 70, width - 50
    cols = [x0 + i * (x1 - x0) / (n_cols - 1) for i in range(n_cols)]
    y_top, y_bot = 40, height - 60

    def Y(v): return y_bot - (v / 100) * (y_bot - y_top)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Job Intelligence over time, individual trajectories">']
    parts.append(f'<rect x="{x0}" y="{y_top}" width="{x1-x0}" height="{y_bot-y_top}" fill="{BACK}"/>')
    for v in (0, 25, 50, 75, 100):
        parts.append(f'<line x1="{x0}" y1="{Y(v)}" x2="{x1}" y2="{Y(v)}" stroke="#E4DCCC"/>')
        parts.append(f'<text x="{x0-22}" y="{Y(v)+4}" style="{FONT};font-size:9.5px" fill="{MUTED}">{v}</text>')

    sums = [0.0] * n_cols
    for s in students:
        vals = [s["ji_pre"]] + ([s.get("ji_sameday", s["ji_pre"])] if has_sameday else []) + [s["ji_w4"]]
        back = vals[-1] < vals[0]
        pts = " ".join(f"{cols[i]},{Y(vals[i])}" for i in range(n_cols))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{WARN if back else INK}" '
                      f'stroke-width="1.2" opacity="{0.6 if back else 0.28}"/>')
        for i in range(n_cols):
            sums[i] += vals[i]

    if students:
        means = [s / len(students) for s in sums]
        pts = " ".join(f"{cols[i]},{Y(means[i])}" for i in range(n_cols))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{TEAL}" stroke-width="4"/>')
        for i in range(n_cols):
            parts.append(f'<circle cx="{cols[i]}" cy="{Y(means[i])}" r="7" fill="{LIME}" stroke="{INK}" stroke-width="2"/>')
            parts.append(f'<text x="{cols[i]}" y="{Y(means[i])-15}" text-anchor="middle" '
                          f'style="{FONT};font-size:13px;font-weight:700" fill="{TEAL}">{means[i]:.0f}</text>')
            parts.append(f'<text x="{cols[i]}" y="{height-30}" text-anchor="middle" '
                          f'style="{FONT};font-size:10px;letter-spacing:.08em;text-transform:uppercase" fill="{MUTED}">{labels[i]}</text>')

    parts.append("</svg>")
    return "".join(parts)

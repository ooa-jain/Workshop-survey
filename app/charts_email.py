"""
PNG chart rendering for result emails. Inline SVG is used on the web
result pages (charts_svg.py) but email clients -- Outlook desktop
especially -- render inline SVG inconsistently, so emailed charts are
rendered server-side as PNG and attached inline via Content-ID.
"""

import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

INK = "#17131F"
TEAL = "#5B3DF5"
LIME = "#D6F53F"
MUTED = "#6B6377"
WARN = "#C3312B"


def _fig_to_png_bytes(fig, dpi=170):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def dimension_arrows_png(rows):
    """
    rows: list of {"desc", "left", "right", "points": [{"label","score_0_3"}, ...]}
    Renders all 5 dimensions stacked vertically as arrow tracks, matching
    the on-screen SVG version.
    """
    n = len(rows)
    fig, ax = plt.subplots(figsize=(7.4, 0.95 * n + 0.6))
    ax.set_xlim(0, 3)
    ax.set_ylim(-0.6, n - 0.4)
    ax.axis("off")

    for i, row in enumerate(rows):
        y = n - 1 - i
        pts = row["points"]
        first, last = pts[0], pts[-1]
        delta = last["score_0_3"] - first["score_0_3"]
        colour = TEAL if delta > 0 else (WARN if delta < 0 else MUTED)

        ax.plot([0, 3], [y, y], color="#D8CEBC", lw=3, solid_capstyle="round", zorder=1)
        ax.text(-0.06, y + 0.30, row["left"].upper(), fontsize=8, color=MUTED,
                 ha="left", va="bottom", family="monospace")
        ax.text(3.06, y + 0.30, row["right"].upper(), fontsize=8, color=TEAL,
                 ha="right", va="bottom", family="monospace")
        ax.text(-1.55, y, row["desc"], fontsize=10.5, color=INK, ha="left", va="center",
                 fontweight="bold")

        if len(pts) == 3:
            mid = pts[1]
            ax.scatter([mid["score_0_3"]], [y], s=55, color=LIME, edgecolor=INK,
                       linewidth=1.2, zorder=3)
            ax.text(mid["score_0_3"], y - 0.28, mid["label"], fontsize=7.5, color=MUTED,
                     ha="center", va="top", family="monospace")

        if abs(last["score_0_3"] - first["score_0_3"]) > 0.05:
            ax.annotate("", xy=(last["score_0_3"], y), xytext=(first["score_0_3"], y),
                        arrowprops=dict(arrowstyle="-|>", color=colour, lw=2.2,
                                         mutation_scale=16), zorder=2)
        ax.scatter([first["score_0_3"]], [y], s=60, color=INK, zorder=4)
        ax.text(first["score_0_3"], y + 0.28, first["label"], fontsize=7.5, color=MUTED,
                 ha="center", va="bottom", family="monospace")
        ax.scatter([last["score_0_3"]], [y], s=75, color=LIME, edgecolor=INK,
                   linewidth=1.5, zorder=4)
        ax.text(last["score_0_3"], y + 0.28, last["label"], fontsize=7.5, color=TEAL,
                 ha="center", va="bottom", family="monospace", fontweight="bold")

        sign = "+" if delta > 0 else ("\u2212" if delta < 0 else "")
        ax.text(3.35, y, f"{sign}{abs(delta):.0f}", fontsize=10, color=colour,
                 ha="left", va="center", fontweight="bold", family="monospace")

    fig.subplots_adjust(left=0.30, right=0.87)
    return _fig_to_png_bytes(fig)


def quadrant_png(series):
    """series: same shape as charts_svg.quadrant_svg's `series` argument."""
    fig, ax = plt.subplots(figsize=(6.2, 4.6))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axvline(50, color="#D8CEBC", lw=1)
    ax.axhline(50, color="#D8CEBC", lw=1)
    ax.add_patch(mpatches.Rectangle((0, 50), 50, 50, color=LIME, alpha=0.17, zorder=0))
    ax.text(2, 96, "JOB INTELLIGENT", fontsize=8.5, color="#4A6B00", fontweight="bold", family="monospace")
    ax.text(52, 96, "BUSY STRATEGIST", fontsize=8.5, color=INK, fontweight="bold", family="monospace")
    ax.text(2, 3, "DRIFTING", fontsize=8.5, color=INK, fontweight="bold", family="monospace")
    ax.text(52, 3, "VOLUME APPLICANT", fontsize=8.5, color=INK, fontweight="bold", family="monospace")
    ax.set_xlabel("Job Search (untargeted)  \u2192", fontsize=9, family="monospace", color=MUTED)
    ax.set_ylabel("Job Intelligence (targeted)  \u2192", fontsize=9, family="monospace", color=MUTED)
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#D8CEBC")

    known = [p for p in series if p.get("job_search") is not None]
    sameday = next((p for p in series if p.get("job_search") is None), None)
    if sameday and known:
        pre = known[0]
        ax.plot([pre["job_search"], pre["job_search"]],
                 [pre["job_intelligence"], sameday["job_intelligence"]],
                 linestyle=(0, (3, 4)), color=TEAL, lw=1.8, alpha=0.7)
        ax.scatter([pre["job_search"]], [sameday["job_intelligence"]], s=50,
                   facecolors="none", edgecolors=TEAL, linewidth=1.8, zorder=4)
        ax.annotate(f'{sameday["label"]} \u00b7 JI {sameday["job_intelligence"]:.0f}',
                    (pre["job_search"], sameday["job_intelligence"]),
                    textcoords="offset points", xytext=(8, 4), fontsize=8, color=TEAL,
                    family="monospace")

    for i in range(len(known) - 1):
        a, b = known[i], known[i + 1]
        ax.annotate("", xy=(b["job_search"], b["job_intelligence"]),
                    xytext=(a["job_search"], a["job_intelligence"]),
                    arrowprops=dict(arrowstyle="-|>", color=TEAL, lw=3, mutation_scale=20))

    for i, p in enumerate(known):
        is_last = i == len(known) - 1
        ax.scatter([p["job_search"]], [p["job_intelligence"]],
                   s=110 if is_last else 80,
                   color=LIME if is_last else INK,
                   edgecolor=INK if is_last else "none", linewidth=2, zorder=5)
        ax.annotate(p["label"], (p["job_search"], p["job_intelligence"]),
                    textcoords="offset points", xytext=(8, 8 if is_last else -12),
                    fontsize=8.5, color=MUTED, family="monospace")

    return _fig_to_png_bytes(fig)

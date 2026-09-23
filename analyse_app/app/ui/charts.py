"""Charts as inline SVG, rendered on the server (#655).

Three rules, all from the brief:

1. **Never a raw scatter.** Two continuous variables get a 2D BINNED heat
   map. A scatter plots one mark per patient, which is a per-patient dataset
   drawn as a picture — and at the edges each mark is an identifiable person.
2. **Suppressed cells render as `<5`**, visibly, not as a gap. A gap reads as
   "no patients"; the two mean opposite things.
3. **Every chart is readable without colour and without sight.** A `<title>`,
   a `<desc>`, and the same figures repeated as a table underneath — so a
   screen reader gets numbers rather than "image".

Nothing here receives patient data. It receives the finalized, suppressed
result, which is already aggregate.
"""
from __future__ import annotations

import html
from typing import Any, Sequence

from .palette import AXIS, GRID, OKABE_ITO, SUPPRESSED_FILL, TEXT, series_style

W, H = 720, 320
PAD_L, PAD_R, PAD_T, PAD_B = 60, 20, 30, 50


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _frame(title: str, desc: str, body: str, *, width=W, height=H) -> str:
    return (
        f'<svg role="img" viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px;height:auto" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<title>{_esc(title)}</title><desc>{_esc(desc)}</desc>'
        f'{body}</svg>')


def histogram(bins: Sequence[dict[str, Any]], *, title: str,
              y_label: str = "patients") -> str:
    """Bar chart of already-binned, already-suppressed counts."""
    if not bins:
        return f'<p class="empty">{_esc(title)}: nothing to show.</p>'

    numeric = [b for b in bins if isinstance(b.get("count"), (int, float))]
    top = max((b["count"] for b in numeric), default=1) or 1
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    bar_w = plot_w / max(len(bins), 1)

    parts = [f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{W - PAD_R}" '
             f'y2="{PAD_T + plot_h}" stroke="{AXIS}"/>']
    for i, b in enumerate(bins):
        x = PAD_L + i * bar_w
        count = b.get("count")
        suppressed = not isinstance(count, (int, float))
        h = plot_h * (0 if suppressed else count / top)
        fill = SUPPRESSED_FILL if suppressed else OKABE_ITO[0]
        y = PAD_T + plot_h - (h if not suppressed else plot_h * 0.08)
        height = h if not suppressed else plot_h * 0.08
        parts.append(
            f'<rect x="{x + 2:.1f}" y="{y:.1f}" width="{bar_w - 4:.1f}" '
            f'height="{max(height, 1):.1f}" fill="{fill}" '
            f'stroke="{AXIS}" stroke-width="0.5"/>')
        if suppressed:
            # Visible, not a gap: a gap reads as "no patients".
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{PAD_T + plot_h - 4:.1f}" '
                f'text-anchor="middle" font-size="11" fill="{TEXT}">&lt;5</text>')
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{H - PAD_B + 16}" '
            f'text-anchor="middle" font-size="10" fill="{TEXT}">'
            f'{_esc(b.get("label", ""))}</text>')
    parts.append(f'<text x="4" y="{PAD_T - 10}" font-size="11" '
                 f'fill="{TEXT}">{_esc(y_label)}</text>')
    desc = ("Bar chart. " + "; ".join(
        f'{b.get("label")}: {b.get("count")}' for b in bins[:12]))
    return _frame(title, desc, "".join(parts))


def heatmap(cells: Sequence[Sequence[Any]], rows: Sequence[str],
            cols: Sequence[str], *, title: str) -> str:
    """2D binned heat map — what replaces a scatter plot.

    A scatter plots one mark per patient. That is a per-patient dataset drawn
    as a picture, and at the edges each mark is an identifiable person.
    """
    if not cells:
        return f'<p class="empty">{_esc(title)}: nothing to show.</p>'
    numeric = [v for row in cells for v in row if isinstance(v, (int, float))]
    top = max(numeric, default=1) or 1
    cw = (W - PAD_L - PAD_R) / max(len(cols), 1)
    ch = (H - PAD_T - PAD_B) / max(len(rows), 1)

    parts = []
    for i, row in enumerate(rows):
        for j, col in enumerate(cols):
            v = cells[i][j]
            x, y = PAD_L + j * cw, PAD_T + i * ch
            if isinstance(v, (int, float)):
                # single-hue ramp: lightness carries magnitude, so it survives
                # greyscale printing and colour-blindness alike
                alpha = 0.12 + 0.88 * (v / top)
                fill = f'rgba(0,114,178,{alpha:.3f})'
                label = str(v)
            else:
                fill, label = SUPPRESSED_FILL, "&lt;5"
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw - 1:.1f}" '
                f'height="{ch - 1:.1f}" fill="{fill}" stroke="#FFFFFF"/>')
            parts.append(
                f'<text x="{x + cw / 2:.1f}" y="{y + ch / 2 + 4:.1f}" '
                f'text-anchor="middle" font-size="11" fill="{TEXT}">{label}</text>')
        parts.append(f'<text x="4" y="{PAD_T + i * ch + ch / 2 + 4:.1f}" '
                     f'font-size="10" fill="{TEXT}">{_esc(row)}</text>')
    for j, col in enumerate(cols):
        parts.append(f'<text x="{PAD_L + j * cw + cw / 2:.1f}" y="{H - PAD_B + 16}" '
                     f'text-anchor="middle" font-size="10" fill="{TEXT}">'
                     f'{_esc(col)}</text>')
    return _frame(title, f"Heat map of counts, {len(rows)} by {len(cols)}.",
                  "".join(parts))


def curve(series: Sequence[dict[str, Any]], *, title: str,
          x_label: str = "days since index event") -> str:
    """Mean per time bin with a confidence band, one line per group."""
    points = [p for s in series for p in s["points"]
              if isinstance(p.get("mean"), (int, float))]
    if not points:
        return f'<p class="empty">{_esc(title)}: nothing to show yet.</p>'
    lo = min(p.get("ci_low", p["mean"]) for p in points)
    hi = max(p.get("ci_high", p["mean"]) for p in points)
    span = (hi - lo) or 1.0
    n_x = max(len(s["points"]) for s in series)
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def sx(i): return PAD_L + (plot_w * i / max(n_x - 1, 1))
    def sy(v): return PAD_T + plot_h - plot_h * ((v - lo) / span)

    parts = [f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{W - PAD_R}" '
             f'y2="{PAD_T + plot_h}" stroke="{AXIS}"/>',
             f'<line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" '
             f'y2="{PAD_T + plot_h}" stroke="{AXIS}"/>']
    for k, s in enumerate(series):
        style = series_style(k)
        pts = [(i, p) for i, p in enumerate(s["points"])
               if isinstance(p.get("mean"), (int, float))]
        if not pts:
            continue
        if all(p.get("ci_low") is not None for _, p in pts):
            band = " ".join(f"{sx(i):.1f},{sy(p['ci_high']):.1f}" for i, p in pts)
            band += " " + " ".join(
                f"{sx(i):.1f},{sy(p['ci_low']):.1f}" for i, p in reversed(pts))
            parts.append(f'<polygon points="{band}" fill="{style["colour"]}" '
                         f'opacity="0.15"/>')
        line = " ".join(f"{sx(i):.1f},{sy(p['mean']):.1f}" for i, p in pts)
        parts.append(
            f'<polyline points="{line}" fill="none" stroke="{style["colour"]}" '
            f'stroke-width="2" stroke-dasharray="{style["dash"]}"/>')
        parts.append(
            f'<text x="{W - PAD_R - 4}" y="{PAD_T + 14 + k * 14}" '
            f'text-anchor="end" font-size="11" fill="{style["colour"]}">'
            f'{_esc(s.get("name", ""))}</text>')
    parts.append(f'<text x="{PAD_L}" y="{H - 8}" font-size="11" '
                 f'fill="{TEXT}">{_esc(x_label)}</text>')
    return _frame(title, "Mean per time period with a confidence band.",
                  "".join(parts))


def data_table(headers: Sequence[str], rows: Sequence[Sequence[Any]],
               *, caption: str) -> str:
    """The same figures as a table, always rendered with the chart.

    This is the accessibility requirement and the "how to read this"
    requirement at once: a screen reader gets numbers rather than "image",
    and a sighted reader who distrusts a picture can check it.
    """
    head = "".join(f"<th scope=\"col\">{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>"
        for r in rows)
    return (f'<table class="figures"><caption>{_esc(caption)}</caption>'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')

"""
UPS Labor Contracts — Corpus Distribution Visualizer
======================================================
Pure Python 3 stdlib only. Outputs a self-contained HTML + SVG report.
Open the generated HTML file in any browser.
"""

from __future__ import annotations
import json, math, html, textwrap, statistics as _stats
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

# ── Load corpus summary ────────────────────────────────────────────────────────
SUMMARY_PATH  = Path(r"c:\Users\SKP0MRS\Documents\UPS\UPS_Labor_Contracts_1\json_schemas\corpus_summary.json")
OUTPUT_PATH   = SUMMARY_PATH.parent / "corpus_visualization.html"
ENRICHED_DIR  = SUMMARY_PATH.parent / "enriched_entities"
SIGMA         = 3  # standard deviations used for monetary range representation

with open(SUMMARY_PATH, encoding="utf-8") as f:
    corpus = json.load(f)

docs = corpus["documents"]

# ── Load enriched entities and update corpus_summary.json ─────────────────────
_QUALITY_FILTER = {
    "extraction_method":        "pypdf-tier0",
    "min_words_per_page":       30,
    "min_articles_plus_sections": 1,
}

enriched_by_file: dict[str, dict] = {}
if ENRICHED_DIR.exists():
    for _ep in sorted(ENRICHED_DIR.glob("*.json")):
        _ent = json.loads(_ep.read_text(encoding="utf-8"))
        enriched_by_file[_ent["source_file"]] = _ent

_qualified    = [d["source_file"] for d in docs if d["source_file"]     in enriched_by_file]
_skipped      = [d["source_file"] for d in docs if d["source_file"] not in enriched_by_file]
_with_wages   = [src for src, e in enriched_by_file.items() if e.get("wage_value_stats")]
_with_benefits = [src for src, e in enriched_by_file.items() if e.get("benefit_value_stats")]

_per_doc: dict[str, dict] = {}
for _src, _ent in enriched_by_file.items():
    _ws = _ent.get("wage_value_stats")
    _bs = _ent.get("benefit_value_stats")
    if _ws or _bs:
        _per_doc[_src] = {}
        if _ws:
            _per_doc[_src]["wage_value_stats"]    = _ws
        if _bs:
            _per_doc[_src]["benefit_value_stats"] = _bs

corpus["enrichment"] = {
    "enriched_at":                  datetime.now(timezone.utc).isoformat(),
    "sigma_used":                   SIGMA,
    "quality_filter":               _QUALITY_FILTER,
    "qualified_count":              len(_qualified),
    "skipped_count":                len(_skipped),
    "skipped_documents":            _skipped,
    "documents_with_wage_stats":    len(_with_wages),
    "documents_with_benefit_stats": len(_with_benefits),
    "per_document":                 _per_doc,
}

with open(SUMMARY_PATH, "w", encoding="utf-8") as _jf:
    json.dump(corpus, _jf, indent=2, ensure_ascii=False)
print(f"corpus_summary.json updated with enrichment block ({len(_qualified)} qualified, {len(_skipped)} skipped).")


# ══════════════════════════════════════════════════════════════════════════════
# SVG Helper Functions
# ══════════════════════════════════════════════════════════════════════════════

def _esc(s):
    return html.escape(str(s))

def _color_palette(n: int) -> list[str]:
    """Generate n distinct colors using a fixed hue wheel."""
    colors = [
        "#2563EB","#0891B2","#059669","#D97706","#DC2626","#7C3AED",
        "#DB2777","#0284C7","#16A34A","#CA8A04","#9333EA","#0F766E",
        "#B45309","#4F46E5","#BE185D","#047857","#1D4ED8","#7E22CE",
        "#065F46","#92400E","#1E40AF","#6B21A8","#14532D","#78350F",
        "#1E3A5F","#4A1942","#134E4A","#451A03","#172554","#3B0764",
    ]
    return [colors[i % len(colors)] for i in range(n)]

def horiz_bar_chart(
    labels: list[str],
    values: list[float],
    title: str,
    colors: list[str] | None = None,
    width: int = 700,
    bar_height: int = 28,
    gap: int = 6,
    label_width: int = 260,
    value_suffix: str = "",
    subtitle: str = "",
) -> str:
    """Return an SVG string for a horizontal bar chart."""
    n = len(labels)
    if n == 0:
        return ""

    max_val = max(values) if max(values) > 0 else 1
    chart_w  = width - label_width - 60  # room for bar + value text
    chart_h  = n * (bar_height + gap) + 10
    svg_h    = chart_h + 60
    if colors is None:
        colors = _color_palette(n)

    rows = []
    for i, (lbl, val) in enumerate(zip(labels, values)):
        y      = 50 + i * (bar_height + gap)
        bar_w  = max(2, int(val / max_val * chart_w))
        color  = colors[i % len(colors)]
        short  = textwrap.shorten(str(lbl).replace("_", " ").title(), 34)
        rows.append(f"""
  <text x="{label_width - 6}" y="{y + bar_height // 2 + 5}" text-anchor="end"
        font-size="12" fill="#334155">{_esc(short)}</text>
  <rect x="{label_width}" y="{y}" width="{bar_w}" height="{bar_height}"
        fill="{color}" rx="3"/>
  <text x="{label_width + bar_w + 5}" y="{y + bar_height // 2 + 5}"
        font-size="12" fill="#475569">{_esc(f'{val:,.0f}')}{_esc(value_suffix)}</text>""")

    sub_el = f'<text x="{width//2}" y="38" text-anchor="middle" font-size="12" fill="#64748B">{_esc(subtitle)}</text>' if subtitle else ""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{svg_h}" style="font-family:system-ui,sans-serif">
  <text x="{width//2}" y="22" text-anchor="middle" font-size="15" font-weight="600" fill="#1E293B">{_esc(title)}</text>
  {sub_el}
  {''.join(rows)}
</svg>"""


def pie_chart(
    labels: list[str],
    values: list[float],
    title: str,
    colors: list[str] | None = None,
    size: int = 380,
) -> str:
    """Return an SVG string for a pie / donut chart."""
    n = len(labels)
    if n == 0:
        return ""
    total = sum(values)
    if total == 0:
        return ""

    if colors is None:
        colors = _color_palette(n)

    cx, cy, r, r_inner = size // 2, size // 2, size // 2 - 40, size // 2 - 90
    slices = []
    angle  = -math.pi / 2  # start from top

    for i, (lbl, val) in enumerate(zip(labels, values)):
        sweep    = 2 * math.pi * val / total
        x1 = cx + r * math.cos(angle)
        y1 = cy + r * math.sin(angle)
        x2 = cx + r * math.cos(angle + sweep)
        y2 = cy + r * math.sin(angle + sweep)
        xi1 = cx + r_inner * math.cos(angle)
        yi1 = cy + r_inner * math.sin(angle)
        xi2 = cx + r_inner * math.cos(angle + sweep)
        yi2 = cy + r_inner * math.sin(angle + sweep)
        large = 1 if sweep > math.pi else 0
        color = colors[i % len(colors)]
        pct   = val / total * 100
        mid_a = angle + sweep / 2
        lx = cx + (r + 18) * math.cos(mid_a)
        ly = cy + (r + 18) * math.sin(mid_a)
        label_el = ""
        if pct >= 6:
            label_el = f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="11" fill="#1E293B" font-weight="500">{pct:.0f}%</text>'
        path = (f'M {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} '
                f'L {xi2:.2f} {yi2:.2f} A {r_inner} {r_inner} 0 {large} 0 {xi1:.2f} {yi1:.2f} Z')
        slices.append(f'<path d="{path}" fill="{color}" stroke="white" stroke-width="2"/>{label_el}')
        angle += sweep

    # Legend
    legend = []
    leg_x, leg_y0, leg_step = 10, size - n * 18 - 10, 18
    for i, (lbl, val) in enumerate(zip(labels, values)):
        short = textwrap.shorten(str(lbl).replace("_", " ").title(), 28)
        ly = leg_y0 + i * leg_step
        legend.append(
            f'<rect x="{leg_x}" y="{ly}" width="12" height="12" fill="{colors[i % len(colors)]}" rx="2"/>'
            f'<text x="{leg_x + 16}" y="{ly + 10}" font-size="11" fill="#334155">{_esc(short)} ({val:,})</text>'
        )

    # Center label
    center_label = (
        f'<text x="{cx}" y="{cy - 8}" text-anchor="middle" font-size="22" font-weight="700" fill="#1E293B">{int(total)}</text>'
        f'<text x="{cx}" y="{cy + 12}" text-anchor="middle" font-size="12" fill="#64748B">total</text>'
    )

    svg_h = size + 30
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{svg_h}" style="font-family:system-ui,sans-serif">
  <text x="{size//2}" y="20" text-anchor="middle" font-size="15" font-weight="600" fill="#1E293B">{_esc(title)}</text>
  {''.join(slices)}
  {center_label}
  {''.join(legend)}
</svg>"""


def scatter_chart(
    x_vals: list[float],
    y_vals: list[float],
    labels: list[str],
    colors: list[str],
    title: str,
    x_label: str,
    y_label: str,
    width: int = 600,
    height: int = 400,
) -> str:
    """Return an SVG scatter plot."""
    pad_l, pad_r, pad_t, pad_b = 70, 30, 40, 50
    plot_w = width  - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    max_x = max(x_vals) if x_vals else 1
    max_y = max(y_vals) if y_vals else 1

    def px(v): return pad_l + v / max_x * plot_w
    def py(v): return pad_t + plot_h - v / max_y * plot_h

    # Axes
    axes = (
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+plot_h}" stroke="#CBD5E1" stroke-width="1"/>'
        f'<line x1="{pad_l}" y1="{pad_t+plot_h}" x2="{pad_l+plot_w}" y2="{pad_t+plot_h}" stroke="#CBD5E1" stroke-width="1"/>'
        f'<text x="{width//2}" y="{height-6}" text-anchor="middle" font-size="12" fill="#64748B">{_esc(x_label)}</text>'
        f'<text transform="rotate(-90,14,{height//2})" x="14" y="{height//2}" text-anchor="middle" font-size="12" fill="#64748B">{_esc(y_label)}</text>'
    )

    # X ticks
    ticks = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        tv = frac * max_x
        tx = px(tv)
        ty = pad_t + plot_h
        ticks.append(f'<line x1="{tx:.1f}" y1="{ty}" x2="{tx:.1f}" y2="{ty+4}" stroke="#94A3B8"/>')
        ticks.append(f'<text x="{tx:.1f}" y="{ty+16}" text-anchor="middle" font-size="10" fill="#94A3B8">{tv:,.0f}</text>')
    for frac in (0.25, 0.5, 0.75, 1.0):
        tv = frac * max_y
        tx_a = pad_l
        ty_a = py(tv)
        ticks.append(f'<line x1="{tx_a-4}" y1="{ty_a:.1f}" x2="{tx_a}" y2="{ty_a:.1f}" stroke="#94A3B8"/>')
        ticks.append(f'<text x="{tx_a-6}" y="{ty_a+4:.1f}" text-anchor="end" font-size="10" fill="#94A3B8">{tv:,.0f}</text>')

    # Points
    points = []
    for xv, yv, lbl, col in zip(x_vals, y_vals, labels, colors):
        short = textwrap.shorten(lbl, 18)
        points.append(
            f'<circle cx="{px(xv):.1f}" cy="{py(yv):.1f}" r="6" fill="{col}" opacity="0.85">'
            f'<title>{_esc(lbl)}\nWords: {xv:,}\nArticles: {yv:,}</title></circle>'
            f'<text x="{px(xv)+8:.1f}" y="{py(yv)+4:.1f}" font-size="9" fill="#475569">{_esc(short)}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" style="font-family:system-ui,sans-serif">
  <text x="{width//2}" y="22" text-anchor="middle" font-size="15" font-weight="600" fill="#1E293B">{_esc(title)}</text>
  {axes}{''.join(ticks)}{''.join(points)}
</svg>"""


def wage_range_chart(
    labels:      list[str],
    means:       list[float],
    range_lows:  list[float],
    range_highs: list[float],
    title:       str,
    subtitle:    str = "",
    currency:    str = "USD",
    unit:        str = "hour",
    width:       int = 780,
    bar_height:  int = 22,
    gap:         int = 8,
    label_width: int = 290,
) -> str:
    """Dumbbell / range chart showing mean ±Nσ wage range per document."""
    n = len(labels)
    if n == 0:
        return ""
    all_vals   = means + range_lows + range_highs
    data_min   = max(0.0, min(all_vals))
    data_max   = max(all_vals)
    data_range = data_max - data_min or 1
    chart_w    = width - label_width - 70
    svg_h      = n * (bar_height + gap) + 80 + (20 if subtitle else 0)

    def xp(v):
        return label_width + max(0.0, (v - data_min) / data_range) * chart_w

    # X axis grid + tick labels
    ticks = []
    n_ticks = 5
    y_axis_top    = 50 + (20 if subtitle else 0)
    y_axis_bottom = y_axis_top + n * (bar_height + gap)
    for i in range(n_ticks):
        tv  = data_min + i / (n_ticks - 1) * data_range
        tx  = xp(tv)
        ticks.append(
            f'<line x1="{tx:.1f}" y1="{y_axis_top}" x2="{tx:.1f}" y2="{y_axis_bottom}" '
            f'stroke="#E2E8F0" stroke-width="1"/>'
            f'<text x="{tx:.1f}" y="{y_axis_bottom + 16}" text-anchor="middle" '
            f'font-size="10" fill="#94A3B8">${tv:.2f}</text>'
        )

    # Axis baseline
    ticks.append(
        f'<line x1="{label_width}" y1="{y_axis_top}" x2="{label_width}" y2="{y_axis_bottom}" '
        f'stroke="#CBD5E1" stroke-width="1.5"/>'
    )

    rows = []
    for i, (lbl, mean, lo, hi) in enumerate(zip(labels, means, range_lows, range_highs)):
        y_mid = y_axis_top + i * (bar_height + gap) + bar_height // 2
        short = textwrap.shorten(str(lbl), 42)
        x_lo  = xp(max(lo,  data_min))
        x_hi  = xp(min(hi,  data_max))
        x_mn  = xp(mean)
        bar_w = max(2.0, x_hi - x_lo)
        rows.append(
            # Row label
            f'<text x="{label_width - 6}" y="{y_mid + 4}" text-anchor="end" '
            f'font-size="11" fill="#334155">{_esc(short)}</text>'
            # Range bar (lo → hi)
            f'<rect x="{x_lo:.1f}" y="{y_mid - 5}" width="{bar_w:.1f}" '
            f'height="10" fill="#BFDBFE" rx="3"/>'
            # End caps
            f'<line x1="{x_lo:.1f}" y1="{y_mid - 8}" x2="{x_lo:.1f}" y2="{y_mid + 8}" '
            f'stroke="#2563EB" stroke-width="1.5"/>'
            f'<line x1="{x_hi:.1f}" y1="{y_mid - 8}" x2="{x_hi:.1f}" y2="{y_mid + 8}" '
            f'stroke="#2563EB" stroke-width="1.5"/>'
            # Mean diamond
            f'<polygon points="{x_mn:.1f},{y_mid - 7} {x_mn + 5:.1f},{y_mid} '
            f'{x_mn:.1f},{y_mid + 7} {x_mn - 5:.1f},{y_mid}" fill="#1D4ED8"/>'
            # Mean value label
            f'<text x="{x_mn:.1f}" y="{y_mid - 10}" text-anchor="middle" '
            f'font-size="9" fill="#1D4ED8">${mean:.2f}</text>'
        )

    sub_el = (
        f'<text x="{width // 2}" y="38" text-anchor="middle" font-size="12" '
        f'fill="#64748B">{_esc(subtitle)}</text>'
    ) if subtitle else ""

    legend_y = svg_h - 14
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{svg_h}" '
        f'style="font-family:system-ui,sans-serif">'
        f'<text x="{width // 2}" y="22" text-anchor="middle" font-size="15" '
        f'font-weight="600" fill="#1E293B">{_esc(title)}</text>'
        f'{sub_el}{" ".join(ticks)}{" ".join(rows)}'
        f'<text x="{label_width}" y="{legend_y}" font-size="10" fill="#94A3B8">'
        f'&#9670; mean &nbsp; &#9646; ±{SIGMA}\u03c3 range &nbsp; Currency: {currency}/{unit}</text>'
        f'</svg>'
    )


def heatmap(
    labels_x: list[str],
    labels_y: list[str],
    values:   list[list[int]],   # values[row][col], row=topic, col=doctype
    title:    str,
    width:    int = 700,
) -> str:
    """Return an SVG heatmap."""
    n_rows, n_cols = len(labels_y), len(labels_x)
    cell_w = min(80, (width - 240) // max(n_cols, 1))
    cell_h = 24
    pad_l  = 240
    pad_t  = 60
    svg_w  = pad_l + n_cols * cell_w + 20
    svg_h  = pad_t + n_rows * cell_h + 40

    max_v  = max(v for row in values for v in row) if values else 1

    def heat_color(v, mx):
        if mx == 0: return "#F1F5F9"
        t   = v / mx
        # White → blue
        r   = int(255 - t * (255 - 30))
        g   = int(255 - t * (255 - 64))
        b   = int(255 - t * (255 - 175))
        return f"#{r:02X}{g:02X}{b:02X}"

    # Column headers
    headers = []
    for j, lbl in enumerate(labels_x):
        short  = textwrap.shorten(lbl.replace("_", " ").title(), 10)
        x_pos  = pad_l + j * cell_w + cell_w // 2
        headers.append(
            f'<text transform="rotate(-35,{x_pos},{pad_t-4})" x="{x_pos}" y="{pad_t-4}" '
            f'text-anchor="end" font-size="11" fill="#334155">{_esc(short)}</text>'
        )

    cells = []
    for i, row in enumerate(values):
        y_pos = pad_t + i * cell_h
        short_y = textwrap.shorten(labels_y[i].replace("_", " ").title(), 32)
        cells.append(
            f'<text x="{pad_l-6}" y="{y_pos + cell_h//2 + 4}" text-anchor="end" '
            f'font-size="11" fill="#334155">{_esc(short_y)}</text>'
        )
        for j, val in enumerate(row):
            x_pos = pad_l + j * cell_w
            color = heat_color(val, max_v)
            txt_c = "#1E293B" if val / (max_v or 1) < 0.7 else "#FFFFFF"
            cells.append(
                f'<rect x="{x_pos}" y="{y_pos}" width="{cell_w-1}" height="{cell_h-1}" fill="{color}" rx="2">'
                f'<title>{labels_y[i]} × {labels_x[j]}: {val}</title></rect>'
            )
            if val > 0:
                cells.append(
                    f'<text x="{x_pos + cell_w//2}" y="{y_pos + cell_h//2 + 4}" '
                    f'text-anchor="middle" font-size="10" fill="{txt_c}">{val}</text>'
                )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" style="font-family:system-ui,sans-serif">
  <text x="{svg_w//2}" y="22" text-anchor="middle" font-size="15" font-weight="600" fill="#1E293B">{_esc(title)}</text>
  {''.join(headers)}
  {''.join(cells)}
</svg>"""


# ══════════════════════════════════════════════════════════════════════════════
# Build Charts
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Document-type donut ─────────────────────────────────────────────────────
doc_types  = list(corpus["document_type_distribution"].keys())
doc_counts = list(corpus["document_type_distribution"].values())
chart_doctype = pie_chart(doc_types, doc_counts,
                          "Document Type Distribution", size=400)

# ── 2. Topic frequency bar ────────────────────────────────────────────────────
topic_items  = list(corpus["topic_distribution"].items())
t_labels     = [k for k, _ in topic_items]
t_values     = [v for _, v in topic_items]
chart_topics = horiz_bar_chart(
    t_labels, t_values,
    title="Topic Distribution Across Corpus",
    subtitle="Number of documents containing each topic",
    width=760, bar_height=24, gap=5, label_width=240,
    colors=_color_palette(len(t_labels))
)

# ── 3. Word count per document ────────────────────────────────────────────────
docs_sorted_wc  = sorted(docs, key=lambda d: d["word_count"], reverse=True)
wc_labels  = [d["source_file"].replace(".pdf", "")[:45] for d in docs_sorted_wc]
wc_values  = [d["word_count"] for d in docs_sorted_wc]
dt_colors  = {"Supplemental Agreement": "#2563EB", "Rider": "#059669",
               "National Master Agreement": "#D97706", "Addendum": "#DC2626", "Other": "#7C3AED"}
wc_colors  = [dt_colors.get(d["document_type"], "#94A3B8") for d in docs_sorted_wc]
chart_wc   = horiz_bar_chart(
    wc_labels, wc_values,
    title="Word Count per Document",
    subtitle="Sorted descending — color = document type",
    width=780, bar_height=18, gap=4, label_width=280,
    colors=wc_colors
)

# ── 4. Article + Section count per document ────────────────────────────────────
docs_sorted_art = sorted(docs, key=lambda d: d["article_count"] + d["section_count"], reverse=True)
art_labels = [d["source_file"].replace(".pdf","")[:45] for d in docs_sorted_art]
art_values = [d["article_count"] for d in docs_sorted_art]
sec_values = [d["section_count"] for d in docs_sorted_art]

# Stacked bar (articles + sections) — render as two separate bars side by side
def dual_bar_chart(labels, vals1, vals2, label1, label2, title, subtitle="", width=780):
    n = len(labels)
    max_v = max(max(vals1), max(vals2)) if n > 0 else 1
    bar_h, gap = 13, 12
    label_w = 280
    chart_w = width - label_w - 60
    svg_h = n * (bar_h * 2 + gap) + 80
    colors = ("#2563EB", "#059669")
    rows = []
    for i, (lbl, v1, v2) in enumerate(zip(labels, vals1, vals2)):
        y0 = 55 + i * (bar_h * 2 + gap)
        short = textwrap.shorten(str(lbl), 40)
        w1 = max(2, int(v1 / max_v * chart_w))
        w2 = max(2, int(v2 / max_v * chart_w))
        rows.append(f"""
  <text x="{label_w - 6}" y="{y0 + bar_h + 3}" text-anchor="end" font-size="11" fill="#334155">{_esc(short)}</text>
  <rect x="{label_w}" y="{y0}" width="{w1}" height="{bar_h}" fill="{colors[0]}" rx="2"/>
  <text x="{label_w + w1 + 3}" y="{y0 + bar_h - 2}" font-size="10" fill="#475569">{v1}</text>
  <rect x="{label_w}" y="{y0 + bar_h + 1}" width="{w2}" height="{bar_h}" fill="{colors[1]}" rx="2"/>
  <text x="{label_w + w2 + 3}" y="{y0 + bar_h * 2}" font-size="10" fill="#475569">{v2}</text>""")
    # Legend
    legend = (f'<rect x="{label_w}" y="24" width="12" height="12" fill="{colors[0]}" rx="2"/>'
              f'<text x="{label_w+16}" y="34" font-size="12" fill="#334155">{_esc(label1)}</text>'
              f'<rect x="{label_w+120}" y="24" width="12" height="12" fill="{colors[1]}" rx="2"/>'
              f'<text x="{label_w+136}" y="34" font-size="12" fill="#334155">{_esc(label2)}</text>')
    sub_el = f'<text x="{width//2}" y="17" text-anchor="middle" font-size="12" fill="#64748B">{_esc(subtitle)}</text>' if subtitle else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{svg_h}" '
            f'style="font-family:system-ui,sans-serif">'
            f'<text x="{width//2}" y="14" text-anchor="middle" font-size="15" font-weight="600" fill="#1E293B">{_esc(title)}</text>'
            f'{sub_el}{legend}{"".join(rows)}</svg>')

chart_art = dual_bar_chart(
    art_labels, art_values, sec_values,
    "Articles", "Sections",
    title="Articles & Sections per Document",
    subtitle="Blue = Articles, Green = Sections — sorted by total",
    width=780
)

# ── 5. Scatter: word count vs article count ───────────────────────────────────
sc_x   = [d["word_count"] for d in docs]
sc_y   = [d["article_count"] for d in docs]
sc_lbl = [d["source_file"].replace(".pdf","")[:30] for d in docs]
sc_col = [dt_colors.get(d["document_type"], "#94A3B8") for d in docs]
chart_scatter = scatter_chart(
    sc_x, sc_y, sc_lbl, sc_col,
    title="Word Count vs. Article Count",
    x_label="Word Count", y_label="Article Count",
    width=640, height=420
)

# ── 6. Topic × Document-type heatmap ──────────────────────────────────────────
all_doc_types_ordered = ["National Master Agreement", "Supplemental Agreement", "Rider", "Addendum"]
all_topics = list(corpus["topic_distribution"].keys())  # already sorted by frequency

# Build matrix
topic_by_type: dict[str, Counter] = {t: Counter() for t in all_topics}
for d in docs:
    for t in d["key_topics"]:
        if t in topic_by_type:
            topic_by_type[t][d["document_type"]] += 1

hm_values = [
    [topic_by_type[t].get(dt, 0) for dt in all_doc_types_ordered]
    for t in all_topics
]
chart_heatmap = heatmap(
    all_doc_types_ordered, all_topics, hm_values,
    title="Topic Coverage by Document Type",
    width=720
)

# ── 7. Pages per document ─────────────────────────────────────────────────────
docs_sorted_pg = sorted(docs, key=lambda d: d["page_count"], reverse=True)
pg_labels = [d["source_file"].replace(".pdf","")[:45] for d in docs_sorted_pg]
pg_values = [d["page_count"] for d in docs_sorted_pg]
pg_colors = [dt_colors.get(d["document_type"], "#94A3B8") for d in docs_sorted_pg]
chart_pages = horiz_bar_chart(
    pg_labels, pg_values,
    title="Page Count per Document",
    subtitle="Color = document type",
    width=780, bar_height=18, gap=4, label_width=280,
    colors=pg_colors
)

# ── 8. Per-document topic count distribution ─────────────────────────────────
topic_cnt_per_doc = Counter(len(d["key_topics"]) for d in docs)
tc_labels = [f"{k} topic{'s' if k != 1 else ''}" for k in sorted(topic_cnt_per_doc)]
tc_values = [topic_cnt_per_doc[k] for k in sorted(topic_cnt_per_doc)]
chart_topic_density = horiz_bar_chart(
    tc_labels, tc_values,
    title="Topic Density per Document",
    subtitle="How many distinct topics each document covers",
    width=600, bar_height=26, gap=6, label_width=160,
    colors=_color_palette(len(tc_labels))
)
# ── 9. Wage value range chart (mean ± 3σ per document) ───────────────────
_wage_rows = []
for d in docs:
    ent = enriched_by_file.get(d["source_file"])
    if not ent:
        continue
    ws = ent.get("wage_value_stats")
    if not ws or not isinstance(ws, dict):
        continue
    # Flat stats block (single unit) or multi-unit dict → prefer "hour", else first
    block = ws if "mean" in ws else ws.get("hour") or next(iter(ws.values()), None)
    if block and block.get("n", 0) >= 1:
        _wage_rows.append((
            d["source_file"].replace(".pdf", "")[:45],
            block["mean"],
            block["range_low"],
            block["range_high"],
        ))

_wage_rows.sort(key=lambda r: r[1])  # ascending by mean

chart_wage_range = ""
if _wage_rows:
    chart_wage_range = wage_range_chart(
        [r[0] for r in _wage_rows],
        [r[1] for r in _wage_rows],
        [r[2] for r in _wage_rows],
        [r[3] for r in _wage_rows],
        title=f"Hourly Wage Distribution per Contract (\u00b1{SIGMA}\u03c3 Range)",
        subtitle=(
            f"Diamond = mean hourly wage \u00b7 Bar = \u00b1{SIGMA}\u03c3 range "
            f"\u00b7 {len(_wage_rows)} contracts with extracted wage data"
        ),
        width=800,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Build Summary Table
# ══════════════════════════════════════════════════════════════════════════════

def doc_table(docs_list: list[dict]) -> str:
    rows = []
    for d in sorted(docs_list, key=lambda x: x["source_file"]):
        topics_str = ", ".join(t.replace("_", " ") for t in d["key_topics"][:5])
        if len(d["key_topics"]) > 5:
            topics_str += f" +{len(d['key_topics'])-5}"
        rows.append(
            f'<tr><td class="fname">{_esc(d["source_file"])}</td>'
            f'<td class="dtype">{_esc(d["document_type"])}</td>'
            f'<td class="num">{d["page_count"]}</td>'
            f'<td class="num">{d["word_count"]:,}</td>'
            f'<td class="num">{d["article_count"]}</td>'
            f'<td class="num">{d["section_count"]}</td>'
            f'<td class="num">{d["local_number"] or "—"}</td>'
            f'<td class="region">{_esc(d["region"] or "—")}</td>'
            f'<td class="topics">{_esc(topics_str)}</td></tr>'
        )
    return "\n".join(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Assemble HTML
# ══════════════════════════════════════════════════════════════════════════════

# Legend for doc type colors used in bar charts
legend_html = " ".join(
    f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">'
    f'<svg width="14" height="14"><rect width="14" height="14" fill="{c}" rx="3"/></svg>'
    f'{_esc(dt)}</span>'
    for dt, c in dt_colors.items() if dt != "Other"
)

HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>UPS Labor Contracts — Corpus Distribution</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#F8FAFC;color:#1E293B;padding:24px}}
  h1{{font-size:1.6rem;font-weight:700;color:#0F172A;margin-bottom:4px}}
  .subtitle{{font-size:.95rem;color:#64748B;margin-bottom:28px}}
  .stats{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:32px}}
  .stat-card{{background:#fff;border:1px solid #E2E8F0;border-radius:12px;padding:16px 24px;min-width:160px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
  .stat-card .num{{font-size:2rem;font-weight:700;color:#2563EB}}
  .stat-card .lbl{{font-size:.82rem;color:#64748B;margin-top:4px}}
  .section{{background:#fff;border:1px solid #E2E8F0;border-radius:12px;padding:24px;margin-bottom:28px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
  .section h2{{font-size:1.05rem;font-weight:600;color:#0F172A;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid #E2E8F0}}
  .two-col{{display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start}}
  .legend{{font-size:.8rem;color:#475569;margin-bottom:12px}}
  table{{width:100%;border-collapse:collapse;font-size:.78rem}}
  th{{background:#F1F5F9;padding:8px 10px;text-align:left;font-weight:600;color:#475569;border-bottom:2px solid #CBD5E1;position:sticky;top:0}}
  td{{padding:7px 10px;border-bottom:1px solid #F1F5F9;vertical-align:top}}
  tr:hover td{{background:#F8FAFC}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  td.fname{{font-size:.72rem;color:#475569;max-width:220px;word-break:break-all}}
  td.dtype{{white-space:nowrap}}
  td.topics{{font-size:.72rem;color:#475569}}
  td.region{{font-size:.75rem}}
  .table-wrap{{overflow-x:auto;max-height:480px;overflow-y:auto;border-radius:8px;border:1px solid #E2E8F0}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.72rem;font-weight:500}}
  footer{{margin-top:40px;text-align:center;font-size:.78rem;color:#94A3B8}}
</style>
</head>
<body>

<h1>UPS Labor Contracts — Corpus Distribution</h1>
<p class="subtitle">Generated {corpus["generated_at"][:10]} &nbsp;·&nbsp; {corpus["total_documents"]} documents &nbsp;·&nbsp; Schema {corpus["schema_version"]}</p>

<div class="stats">
  <div class="stat-card"><div class="num">{corpus["total_documents"]}</div><div class="lbl">Documents</div></div>
  <div class="stat-card"><div class="num">{corpus["total_pages"]:,}</div><div class="lbl">Total Pages</div></div>
  <div class="stat-card"><div class="num">{corpus["total_words"]:,}</div><div class="lbl">Total Words</div></div>
  <div class="stat-card"><div class="num">{corpus["total_articles_found"]}</div><div class="lbl">Articles Parsed</div></div>
  <div class="stat-card"><div class="num">{corpus["total_sections_found"]}</div><div class="lbl">Sections Parsed</div></div>
  <div class="stat-card"><div class="num">{len(corpus["topic_distribution"])}</div><div class="lbl">Distinct Topics</div></div>
  <div class="stat-card"><div class="num">0</div><div class="lbl">Failed / Invalid</div></div>
  <div class="stat-card" style="border-color:#2563EB"><div class="num" style="color:#059669">{len(_qualified)}</div><div class="lbl">Enriched Docs</div></div>
  <div class="stat-card" style="border-color:#2563EB"><div class="num" style="color:#059669">{len(_with_wages)}</div><div class="lbl">Wage Stats (±{SIGMA}σ)</div></div>
</div>

<div class="section">
  <h2>Document Type &amp; Topic Coverage</h2>
  <div class="two-col">
    {chart_doctype}
    {chart_topics}
  </div>
</div>

<div class="section">
  <h2>Word Count per Document</h2>
  <div class="legend">Color legend: {legend_html}</div>
  {chart_wc}
</div>

<div class="section">
  <h2>Page Count per Document</h2>
  <div class="legend">Color legend: {legend_html}</div>
  {chart_pages}
</div>

<div class="section">
  <h2>Structural Depth — Articles &amp; Sections per Document</h2>
  {chart_art}
</div>

<div class="section">
  <h2>Topic Density per Document &nbsp;&amp;&nbsp; Word Count vs. Article Count</h2>
  <div class="two-col">
    {chart_topic_density}
    {chart_scatter}
  </div>
</div>

<div class="section">
  <h2>Topic Coverage Heatmap by Document Type</h2>
  <p style="font-size:.8rem;color:#64748B;margin-bottom:12px">Cell value = number of documents of that type containing the topic. Darker = higher frequency.</p>
  {chart_heatmap}
</div>

{f'''
<div class="section">
  <h2>Wage Value Distribution &mdash; Mean &plusmn;{SIGMA}&sigma; Range per Contract</h2>
  <p style="font-size:.8rem;color:#64748B;margin-bottom:12px">
    Contracts where hourly wage parameters were extracted (n={len(_wage_rows)}).
    The bar spans mean &minus; {SIGMA}&sigma; to mean + {SIGMA}&sigma; (covering &approx;99.7% of values under a normal model).
    Diamond marker = mean. Sorted ascending by mean.
  </p>
  {chart_wage_range}
</div>
''' if chart_wage_range else ''}

<div class="section">
  <h2>All Documents — Detail Table</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>File</th><th>Type</th><th style="text-align:right">Pages</th>
          <th style="text-align:right">Words</th><th style="text-align:right">Articles</th>
          <th style="text-align:right">Sections</th><th style="text-align:right">Local</th>
          <th>Region</th><th>Key Topics (first 5)</th>
        </tr>
      </thead>
      <tbody>
        {doc_table(docs)}
      </tbody>
    </table>
  </div>
</div>

<footer>UPS Labor Contracts Entity Corpus &nbsp;·&nbsp; Pure-stdlib SVG charts &nbsp;·&nbsp; No external dependencies</footer>
</body>
</html>
"""

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    f.write(HTML)

print(f"Visualization saved → {OUTPUT_PATH}")
print("Open the HTML file in any browser to view the charts.")

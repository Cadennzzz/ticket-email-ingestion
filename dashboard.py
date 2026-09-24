"""
Local dashboard for ticket-transaction bookkeeping.

Reads directly from transactions.db on every run (no separate export step).

Run with:
    streamlit run dashboard.py
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from crosscheck import find_excel_matches
from db import DB_PATH
from grouping import group_pending_rows, read_existing_event_names, suggest_event_name
from manual_marks import load_marks, mark, unmark

st.set_page_config(page_title="Ticket Transactions", page_icon="🎟️", layout="wide")

# --- Visual identity -------------------------------------------------------
# Base colors live in .streamlit/config.toml; this block adds the card/zone
# styling Streamlit's theme options can't express.
ACCENT = "#2dd4bf"
WARN = "#f59e0b"
POS = "#22c55e"
NEG = "#ef4444"
MUTED = "#8b9ab5"
BORDER = "#1e2a44"
# Chart tokens: every chart color, size and spacing lives here, nowhere
# else. Colors were checked with the dataviz palette validator against the
# zone surface (lightness band, chroma, CVD incl. GOOD vs BAD, contrast).
CHART_SURFACE = "#0f1729"
SPEND_COLOR = "#dc6a50"  # coral: money out (never green)
REVENUE_COLOR = "#12a594"  # teal: money in
GOOD_COLOR = "#10a37f"
BAD_COLOR = "#dc2626"
NEUTRAL_COLOR = "#64748b"  # diverging midpoint: gray, not a hue
NO_SIGNAL_COLOR = "#334155"  # "Other", and categories with too few positions to judge
INK_MARK_COLOR = "#cbd5e1"  # cumulative line and avg dots: ink, not a series hue
GRID_COLOR = "rgba(148,163,184,.10)"
TYPE_TITLE, TYPE_LABEL, TYPE_AXIS = 13, 11, 10  # the only three chart text sizes
BAR_RADIUS = 4
BAR_GAP = 0.45
MIN_POSITIONS = 5  # below this, P&L / fee-rate color is noise, so it's grayed out

st.markdown(
    f"""
<style>
:root {{
  --accent: {ACCENT}; --warn: {WARN}; --pos: {POS}; --neg: {NEG};
  --muted: {MUTED}; --border: {BORDER}; --card: #131d34; --card-2: #0f1729;
  --ink: #e2e8f0;
  --mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
  --lbl: .66rem;  /* every small uppercase label shares this size */
  --lift: inset 0 1px 0 rgba(255,255,255,.035);
}}
.block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1400px; }}
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {{ gap: 1.1rem; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: var(--muted); font-size: .8rem; }}
/* Streamlit pulls markdown up by -1rem to hide a trailing <p> margin; our
   HTML blocks have none, so that pull made cards sit flush on zone edges. */
[data-testid="stMarkdownContainer"]:has(> .stats, > .card, > .banner, > .src-lines) {{ margin-bottom: 0; }}
[class*="st-key-grp-"], [class*="st-key-flag-"] {{ margin-bottom: -.55rem; }}
footer, #MainMenu {{ visibility: hidden; }}

/* Header band */
.hdr {{ display: flex; align-items: flex-end; justify-content: space-between;
  border-bottom: 1px solid var(--border); padding-bottom: .9rem; }}
.hdr h1 {{ font-size: 1.65rem; font-weight: 700; letter-spacing: -.02em; margin: 0; padding: 0; }}
.hdr h1 .tick {{ color: var(--accent); }}
.hdr .meta {{ color: var(--muted); font-family: var(--mono); font-size: .78rem; }}

/* Section heads */
.sec {{ display: flex; align-items: baseline; gap: .6rem; margin: 0 0 .3rem 0; }}
.sec .eyebrow {{ font-family: var(--mono); font-size: var(--lbl); letter-spacing: .14em;
  text-transform: uppercase; color: var(--tone); }}
.sec .title {{ font-size: 1.1rem; font-weight: 650; color: var(--ink); letter-spacing: -.01em; }}
.pill {{ font-family: var(--mono); font-size: .72rem; padding: .1rem .5rem; border-radius: 999px;
  background: color-mix(in srgb, var(--tone) 16%, transparent); color: var(--tone);
  border: 1px solid color-mix(in srgb, var(--tone) 40%, transparent); }}
.sec-sub {{ color: var(--muted); font-size: .8rem; margin: -.1rem 0 .5rem 0; }}

/* Zones = keyed bordered containers, each with a colored top rule */
[class*="st-key-zone-"] {{ background: var(--card-2); border-top: 2px solid var(--zone, var(--border)) !important;
  box-shadow: var(--lift); }}
.st-key-zone-overview, .st-key-zone-lookup {{ --zone: var(--accent); }}
.st-key-zone-pending  {{ --zone: var(--accent); }}
.st-key-zone-review   {{ --zone: var(--warn); }}
.st-key-zone-charts, .st-key-zone-matches, .st-key-zone-all {{ --zone: #334155; }}

/* Stat cards */
.stats {{ display: grid; gap: .6rem; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: .6rem;
  padding: .65rem .85rem .7rem; min-width: 0; min-height: 6.4rem; box-shadow: var(--lift); }}
.card .lbl {{ font-family: var(--mono); font-size: var(--lbl); letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted); }}
.card .val {{ font-family: var(--mono); font-size: 1.55rem; font-weight: 600;
  font-variant-numeric: tabular-nums; margin-top: .15rem; white-space: nowrap; }}
.card .sub {{ color: var(--muted); font-size: .72rem; margin-top: .1rem; }}
.card.warn {{ border-color: color-mix(in srgb, var(--warn) 45%, transparent);
  background: color-mix(in srgb, var(--warn) 7%, var(--card)); }}
.card.warn .val, .card.warn .lbl {{ color: var(--warn); }}
.card.pos {{ border-color: color-mix(in srgb, var(--pos) 40%, transparent); }}
.card.neg {{ border-color: color-mix(in srgb, var(--neg) 40%, transparent); }}
.group-lbl {{ font-family: var(--mono); font-size: var(--lbl); letter-spacing: .14em;
  text-transform: uppercase; color: var(--muted); margin: .1rem 0 .35rem; }}
.pos {{ color: var(--pos); }} .neg {{ color: var(--neg); }} .na {{ color: var(--muted); }}
.card .val.sm {{ font-size: 1.05rem; white-space: normal; line-height: 1.35; padding-top: .2rem; }}

/* Event lookup */
.ev-head {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: .35rem .9rem; margin: .35rem 0 .6rem; }}
.ev-head .name {{ font-size: 1.25rem; font-weight: 650; color: var(--ink); letter-spacing: -.01em; }}
.ev-head .meta {{ font-family: var(--mono); font-size: .78rem; color: var(--muted); }}

/* Banner + callouts */
.banner {{ display: flex; gap: .7rem; align-items: center; padding: .6rem .9rem; border-radius: .6rem;
  border: 1px solid color-mix(in srgb, var(--warn) 40%, transparent);
  background: color-mix(in srgb, var(--warn) 8%, transparent); color: #fde68a; font-size: .86rem; }}
.banner b {{ color: var(--warn); font-family: var(--mono); }}
.banner code {{ background: rgba(0,0,0,.3); color: #fde68a; }}
.callout {{ border-left: 3px solid var(--warn); background: color-mix(in srgb, var(--warn) 8%, transparent);
  color: #fde68a; padding: .45rem .75rem; border-radius: 0 .4rem .4rem 0; font-size: .84rem; margin-bottom: .4rem; }}
.chip {{ display: inline-flex; gap: .45rem; align-items: center; font-size: .82rem; margin-bottom: .3rem; }}
.chip .k {{ font-family: var(--mono); font-size: var(--lbl); letter-spacing: .12em; text-transform: uppercase; color: var(--muted); }}
.chip .v {{ font-family: var(--mono); color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); padding: .08rem .45rem; border-radius: .35rem; }}
.empty {{ color: var(--muted); font-size: .85rem; padding: .5rem 0; }}
.empty::before {{ content: "✓ "; color: var(--pos); }}

/* Expanders: tighter; flagged ones get an amber rule */
[data-testid="stExpander"] details {{ border-color: var(--border); background: var(--card); }}
[data-testid="stExpander"] summary {{ padding-top: .45rem; padding-bottom: .45rem; font-size: .9rem; }}
[class*="st-key-flag-"] [data-testid="stExpander"] details {{ border-left: 3px solid var(--warn); }}
[class*="st-key-flag-"] [data-testid="stExpander"] summary p {{ color: #fde68a; }}
.src-lines {{ margin-top: .15rem; }}
.src-line {{ color: var(--muted); font-family: var(--mono); font-size: .7rem; line-height: 1.55; opacity: .85; }}
.src-line .uid {{ opacity: .6; }}
[data-testid="stExpander"] [data-testid="stMetric"] {{ background: var(--card-2); border: 1px solid var(--border);
  border-radius: .5rem; padding: .45rem .7rem; }}
[data-testid="stMetricLabel"] p {{ font-family: var(--mono); font-size: var(--lbl); letter-spacing: .1em;
  text-transform: uppercase; color: var(--muted); }}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{ font-family: var(--mono); font-size: 1.2rem; font-variant-numeric: tabular-nums; }}

/* Multiselect tags: tinted instead of solid */
[data-baseweb="tag"] {{ background: color-mix(in srgb, var(--accent) 14%, transparent) !important;
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); }}
[data-baseweb="tag"] span, [data-baseweb="tag"] svg {{ color: var(--accent) !important; fill: var(--accent); }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 1.2rem; border-bottom: 1px solid var(--border); }}
.stTabs [data-baseweb="tab"] p {{ font-family: var(--mono); font-size: .8rem; letter-spacing: .04em; }}
</style>
""",
    unsafe_allow_html=True,
)


def section_header(title: str, eyebrow: str, count=None, tone: str = ACCENT, sub: str | None = None) -> None:
    pill = f'<span class="pill">{count}</span>' if count is not None else ""
    sub_html = f'<div class="sec-sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="sec" style="--tone:{tone}"><span class="eyebrow">{eyebrow}</span>'
        f'<span class="title">{title}</span>{pill}</div>{sub_html}',
        unsafe_allow_html=True,
    )


def money_html(value, signed: bool = False) -> str:
    if value is None or pd.isna(value):
        return '<span class="na">N/A</span>'
    if not signed:
        return f"${value:,.2f}"
    cls = "pos" if value > 0 else "neg" if value < 0 else "na"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f'<span class="{cls}">{sign}${abs(value):,.2f}</span>'


def stat_card(label: str, value_html: str, sub: str | None = None, tone: str | None = None, small: bool = False) -> str:
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return (
        f'<div class="card {tone or ""}"><div class="lbl">{label}</div>'
        f'<div class="val{" sm" if small else ""}">{value_html}</div>{sub_html}</div>'
    )


def stat_grid(cards: list[str], cols: int) -> str:
    return f'<div class="stats" style="grid-template-columns:repeat({cols},minmax(0,1fr))">{"".join(cards)}</div>'


def empty_state(msg: str) -> None:
    st.markdown(f'<div class="empty">{msg}</div>', unsafe_allow_html=True)


CHART_FONT = "JetBrains Mono, SF Mono, Menlo, monospace"
CHART_CONFIG = {"displayModeBar": False}  # the hover toolbar sat on top of the legend


def style_chart(fig, height: int = 300):
    has_title = bool(fig.layout.title.text)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=CHART_FONT, size=TYPE_LABEL, color=MUTED),
        margin=dict(l=4, r=8, t=40 if has_title else 30, b=4),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, title_text="",
                    font=dict(size=TYPE_LABEL, color="#cbd5e1"), itemclick=False, itemdoubleclick=False),
        hoverlabel=dict(bgcolor="#131d34", bordercolor=BORDER, font=dict(family=CHART_FONT, size=TYPE_LABEL, color="#e2e8f0")),
        bargap=BAR_GAP,
        height=height,
    )
    if has_title:
        fig.update_layout(title=dict(font=dict(size=TYPE_TITLE, color="#cbd5e1"), x=0, xanchor="left", y=0.98, yanchor="top"))
    fig.update_traces(marker_cornerradius=BAR_RADIUS, marker_line_width=0, selector=dict(type="bar"))
    axis = dict(showline=False, ticks="", title_text="", tickfont=dict(size=TYPE_AXIS), gridcolor=GRID_COLOR,
                zeroline=False, title_font=dict(size=TYPE_LABEL, color=MUTED))
    fig.update_xaxes(showgrid=False, **axis)
    fig.update_yaxes(showgrid=True, **axis)
    return fig


def month_ticktext(months) -> list[str]:
    """One row per tick; the year only on January (and the first tick, so
    the axis never starts without one)."""
    return [m.strftime("%b %Y") if i == 0 or m.month == 1 else m.strftime("%b") for i, m in enumerate(months)]


def zero_aligned_ranges(*extents, pad: float = 1.25) -> list[list[float]]:
    """Ranges for several y-axes that put zero at the same height, so a line
    on a secondary axis can't seem to cross the bars' baseline where it
    doesn't. Picks the shared zero position that wastes the least space."""
    ext = [(min(lo, 0), max(hi, 0)) for lo, hi in extents]

    def spans(f):
        return [max(-lo / f, hi / (1 - f)) for lo, hi in ext]

    f = min((i / 100 for i in range(5, 96)),
            key=lambda f: sum(s / ((hi - lo) or 1) for s, (lo, hi) in zip(spans(f), ext)))
    return [[-f * s * pad, (1 - f) * s * pad] for s in spans(f)]


def fold_platforms(frame: pd.DataFrame, volume: pd.Series, top_n: int = 6, fold=frozenset()) -> pd.DataFrame:
    """Per-platform count/volume for the platform charts, indexed by a
    casefolded key. Case variants ('Axs'/'AXS') merge under their most common
    spelling, a missing platform is 'Unspecified', and keys in `fold` plus
    everything past the top `top_n` by volume fold into 'Other', pinned
    last. Chart-only; the underlying platform values are left as they are."""
    d = frame.assign(platform=frame["platform"].fillna("Unspecified").astype(str).str.strip(), volume=volume)
    d["key"] = d["platform"].str.casefold()
    label = d.groupby("key")["platform"].agg(lambda s: s.value_counts().index[0])
    summary = (
        d.groupby("key").agg(count=("id", "count"), volume=("volume", "sum"))
        .assign(platform=label, is_other=False)
        .sort_values("volume", ascending=False)
    )
    named = summary[~summary.index.isin(fold)]
    rest = pd.concat([named.iloc[top_n:], summary[summary.index.isin(fold)]])
    summary = named.iloc[:top_n]
    if len(rest):
        other = pd.DataFrame(
            {"count": [rest["count"].sum()], "volume": [rest["volume"].sum()],
             "platform": [f"Other ({len(rest)})"], "is_other": [True]},
            index=["__other__"],
        )
        summary = pd.concat([summary, other])
    return summary.assign(avg=summary["volume"] / summary["count"])


def platform_chart(summary: pd.DataFrame, title: str, bins: list[tuple[str, str, str]]):
    """Horizontal bars = dollar volume, colored by `summary['bin']` (one legend
    entry per bin in `bins`: (bin, legend label, color)); a dot on a top axis
    = average $ per transaction. Expects columns platform, tick, volume,
    count, avg, bin, detail; rows already in display order."""
    fig = go.Figure()
    for b, name, color in bins:
        part = summary[summary["bin"] == b]
        if part.empty:
            continue
        fig.add_bar(
            x=part["volume"], y=part["tick"], orientation="h", name=name, marker_color=color,
            customdata=part[["platform", "count", "detail"]],
            hovertemplate="<b>%{customdata[0]}</b><br>$%{x:,.0f} volume · %{customdata[1]:,} transactions"
            "<br>%{customdata[2]}<extra></extra>",
        )
    fig.add_scatter(
        x=summary["avg"], y=summary["tick"], xaxis="x2", mode="markers", name="Avg $ / transaction",
        marker=dict(size=8, color=INK_MARK_COLOR, line=dict(width=2, color=CHART_SURFACE)),
        customdata=summary[["platform"]], hovertemplate="<b>%{customdata[0]}</b><br>avg $%{x:,.0f} per transaction<extra></extra>",
    )
    fig.update_layout(title_text=title, barmode="relative")
    style_chart(fig, height=34 * len(summary) + 150)
    fig.update_layout(
        margin=dict(t=74, b=40),  # bottom room for the legend under the axis title
        legend=dict(orientation="h", yref="container", y=0, yanchor="bottom", x=0, xanchor="left"),
        xaxis2=dict(overlaying="x", side="top", rangemode="tozero", tickformat="$~s", nticks=4, showgrid=False,
                    showline=False, ticks="", tickfont=dict(size=TYPE_AXIS),
                    title=dict(text="Avg $ per transaction (dot)", font=dict(size=TYPE_LABEL, color=MUTED))),
    )
    fig.update_layout(xaxis=dict(showgrid=True, rangemode="tozero", tickformat="$~s", nticks=5,
                                 title_text="Dollar volume, USD (bar)"))
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=summary["tick"].tolist()[::-1],
                     tickfont=dict(size=TYPE_AXIS, color="#cbd5e1"))
    return fig


MONEY_COL = st.column_config.NumberColumn(format="$%.2f")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    )
    return cur.fetchone() is not None


@st.cache_data(ttl=5)
def load_transactions() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        if not table_exists(conn, "transactions"):
            return pd.DataFrame()
        df = pd.read_sql_query("SELECT * FROM transactions", conn)
    finally:
        conn.close()
    return df


@st.cache_data(ttl=5)
def load_matches() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        if not table_exists(conn, "matches"):
            return pd.DataFrame()
        df = pd.read_sql_query("SELECT * FROM matches", conn)
    finally:
        conn.close()
    return df


@st.cache_data(ttl=60)
def load_existing_event_names() -> set:
    try:
        return read_existing_event_names()
    except FileNotFoundError:
        return set()


df = load_transactions()

if df.empty:
    st.markdown(
        '<div class="hdr"><h1><span class="tick">▍</span>Ticket Ledger</h1></div>', unsafe_allow_html=True
    )
    st.info("No data yet — run `python ingest.py` to scan your inbox and populate transactions.db.")
    st.stop()

df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
df["total_price"] = pd.to_numeric(df["total_price"], errors="coerce")
df["needs_review"] = df["needs_review"].astype(bool)
df["source"] = df["source"].fillna("email")
df["promoted"] = df["promoted"].fillna(0).astype(bool)

# Real metrics (header, time series, platform breakdown) only reflect the
# canonical Excel-sourced dataset. Scraped emails (source='email') are
# captured for review but excluded here until explicitly promoted.
excel_df = df[df["source"] == "excel"].copy()


def after_fees(sells: pd.DataFrame) -> pd.Series:
    """What each sell row actually paid out. SL "Total Sale After Fees" is
    Tickets Sold × Sell Price in the workbook (and what match.py uses for
    matched-pair P&L); the DB's total_price is Gross Sale, so rebuild it."""
    price = pd.to_numeric(sells["price_per_ticket"], errors="coerce")
    return (sells["quantity"] * price).fillna(sells["total_price"])


def _records(frame: pd.DataFrame) -> list:
    return frame.astype(object).where(pd.notnull(frame), None).to_dict("records")


# Scraped rows that already have a matching line in the sheet were handled
# by hand; they're listed separately instead of as needing action. Rows the
# user marked as recorded (manual_marks.csv, for sheet entries the matcher
# can't link) leave Pending the same way, but are listed on their own.
unpromoted_df = df[(df["source"] == "email") & (~df["promoted"])]
excel_match = find_excel_matches(_records(unpromoted_df), _records(excel_df))
manual_marks = load_marks()
auto_matched = unpromoted_df["id"].isin(excel_match)
manually_marked = ~auto_matched & unpromoted_df["raw_email_uid"].astype(str).isin(manual_marks)
recorded_df = unpromoted_df[auto_matched]
manual_df = unpromoted_df[manually_marked]
pending_df = unpromoted_df[~auto_matched & ~manually_marked]
pending_count = len(pending_df)

buys = excel_df[excel_df["transaction_type"] == "buy"]
sells = excel_df[excel_df["transaction_type"] == "sell"]

total_bought = buys["quantity"].sum()
total_sold = sells["quantity"].sum()
total_spent = buys["total_price"].sum()
sell_after_fees = after_fees(sells)
total_revenue = sell_after_fees.sum()
has_both_sides = buys["total_price"].notna().any() and sell_after_fees.notna().any()
review_count = int(df["needs_review"].sum())

# --- Header band ---------------------------------------------------------------
st.markdown(
    f'<div class="hdr"><h1><span class="tick">▍</span>Ticket Ledger</h1>'
    f'<div class="meta">{html.escape(DB_PATH.name)} · {len(df):,} rows · {len(excel_df):,} in working sheet</div></div>',
    unsafe_allow_html=True,
)

if pending_count:
    st.markdown(
        f'<div class="banner"><span>⚠</span><div><b>{pending_count}</b> scraped transaction(s) pending review '
        "(source='email', not yet promoted or recorded in the sheet) — excluded from the metrics below. "
        "Run <code>python review_pending.py</code> to list them.</div></div>",
        unsafe_allow_html=True,
    )

# --- Overview ------------------------------------------------------------------
with st.container(border=True, key="zone-overview"):
    section_header("Overview", "01 · working sheet")
    profit = (total_revenue - total_spent) if has_both_sides else None
    profit_tone = None if profit is None else ("pos" if profit >= 0 else "neg")
    vcol, mcol, rcol = st.columns([2, 3, 1], gap="medium")
    with vcol:
        st.markdown('<div class="group-lbl">Volume</div>', unsafe_allow_html=True)
        st.markdown(
            stat_grid(
                [
                    stat_card("Tickets bought", f"{total_bought:,.0f}" if pd.notna(total_bought) else "0"),
                    stat_card("Tickets sold", f"{total_sold:,.0f}" if pd.notna(total_sold) else "0"),
                ],
                2,
            ),
            unsafe_allow_html=True,
        )
    with mcol:
        st.markdown('<div class="group-lbl">Money</div>', unsafe_allow_html=True)
        st.markdown(
            stat_grid(
                [
                    stat_card("Total spent", money_html(total_spent if pd.notna(total_spent) else 0.0)),
                    stat_card(
                        "Total revenue",
                        money_html(total_revenue if pd.notna(total_revenue) else 0.0),
                        sub="after fees",
                    ),
                    stat_card(
                        "Realized profit",
                        money_html(profit, signed=True),
                        sub="naive · revenue − spend",
                        tone=profit_tone,
                    ),
                ],
                3,
            ),
            unsafe_allow_html=True,
        )
    with rcol:
        st.markdown('<div class="group-lbl">Attention</div>', unsafe_allow_html=True)
        st.markdown(
            stat_card("Needs review", str(review_count), tone="warn" if review_count else None),
            unsafe_allow_html=True,
        )


# --- Event lookup --------------------------------------------------------------
# Mirrors the workbook's "Event Lookup" tab (fed by its Inventory Dashboard),
# computed from the source='excel' rows: BL (buys) keyed by event name, with
# SL (sells) summed against it.
def build_event_lookup(excel_df: pd.DataFrame) -> pd.DataFrame:
    bl = excel_df[excel_df["transaction_type"] == "buy"].copy()
    sl = excel_df[excel_df["transaction_type"] == "sell"].copy()
    if bl.empty:
        return pd.DataFrame()

    bl["price_per_ticket"] = pd.to_numeric(bl["price_per_ticket"], errors="coerce")
    events = bl.groupby("artist_or_event").agg(
        event_date=("event_date", "first"),
        venue=("venue", "first"),
        price_per_ticket=("price_per_ticket", "first"),
        bought=("quantity", "sum"),
        cost=("total_price", "sum"),
    )

    sl["after_fees"] = after_fees(sl)
    sl["transferred"] = sl["quantity"].where(sl["transfer_status"] == "transferred", 0)
    # Excel's SUMIFS matches names case-insensitively ('max styler' counts
    # toward 'Max Styler'), so join on a casefolded key.
    sold = sl.groupby(sl["artist_or_event"].str.strip().str.casefold()).agg(
        sold=("quantity", "sum"), revenue=("after_fees", "sum"), transferred=("transferred", "sum")
    )

    events["key"] = events.index.str.strip().str.casefold()
    events = events.join(sold, on="key", how="left").fillna({"sold": 0, "revenue": 0.0, "transferred": 0})
    events["inv_profit"] = events["revenue"] - events["cost"].fillna(0)
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce")
    today = pd.Timestamp(datetime.now().date())
    events["days"] = (events["event_date"] - today).dt.days
    events["status"] = events.apply(_event_status, axis=1)
    return events


def _event_status(e) -> str:
    """The BL Status formula, minus its first branch: 'Paid Out' comes from
    BL's "Paid out?" checkbox, which isn't imported, so fully sold and
    transferred events read 'Sold and Sent' here."""
    bought, sold, sent = int(e["bought"] or 0), int(e["sold"]), int(e["transferred"])
    to_sell, to_send = max(0, bought - sold), max(0, sold - sent)
    if bought > 0 and bought == sold and sold == sent:
        return "Sold and Sent"
    if bought == sold:
        return f"OOS, Transfer {to_send}"
    if to_send > 0:
        return f"Sell {to_sell}, Transfer {to_send}"
    return f"Sell {to_sell}"


def lookup_options(events: pd.DataFrame) -> list[str]:
    """Upcoming events soonest-first, then past events most-recent-first."""
    upcoming = events[events["days"] >= 0].sort_values("days")
    past = events[~(events["days"] >= 0)].sort_values("days", ascending=False, na_position="last")
    return upcoming.index.tolist() + past.index.tolist()


events_lookup = build_event_lookup(excel_df)

with st.container(border=True, key="zone-lookup"):
    section_header("Event lookup", "02 · lookup", sub="Working sheet only · type to search by event name.")
    if events_lookup.empty:
        empty_state("No working-sheet events to look up yet.")
    else:
        picked = st.selectbox(
            "Event",
            lookup_options(events_lookup),
            index=None,
            placeholder=f"Search {len(events_lookup)} events…",
            label_visibility="collapsed",
            key="event_lookup",
        )
        if picked:
            e = events_lookup.loc[picked]
            date_str = e["event_date"].strftime("%a %b %-d, %Y") if pd.notna(e["event_date"]) else "No date"
            venue = f" · {html.escape(str(e['venue']))}" if e["venue"] else ""
            st.markdown(
                f'<div class="ev-head"><span class="name">{html.escape(picked)}</span>'
                f'<span class="meta">{date_str}{venue}</span></div>',
                unsafe_allow_html=True,
            )

            bought, sold = int(e["bought"] or 0), int(e["sold"])
            if pd.isna(e["days"]):
                days_val, days_sub = '<span class="na">—</span>', "no event date"
            elif e["days"] >= 0:
                days_val, days_sub = f"{int(e['days'])}", "days until event"
            else:
                days_val, days_sub = f"−{abs(int(e['days']))}", "days · event passed"
            status = e["status"]
            status_tone = "pos" if status == "Sold and Sent" else ("neg" if (e["days"] or 0) < 0 and bought > sold else None)
            st.markdown(
                stat_grid(
                    [
                        stat_card("$ / ticket", money_html(e["price_per_ticket"])),
                        stat_card("Sold / total", f"{sold}<span class='na'> / {bought}</span>",
                                  sub=f"{bought - sold} left" if bought > sold else "all sold"),
                        stat_card("Status", f'<span class="{status_tone or ""}">{html.escape(status)}</span>', small=True),
                        stat_card("Days to sell", days_val, sub=days_sub),
                        stat_card("Inv profit", money_html(e["inv_profit"], signed=True),
                                  sub=f"{money_html(e['revenue'])} rev − {money_html(e['cost'])} cost",
                                  tone="pos" if e["inv_profit"] > 0 else "neg" if e["inv_profit"] < 0 else None),
                    ],
                    5,
                ),
                unsafe_allow_html=True,
            )


def source_line_html(row: dict) -> str | None:
    """Muted 'via … · sent to … · <local time>' line for one contributing row.
    None when the row has no email header fields (Excel-sourced rows)."""
    sender_name, recipient, received = row.get("sender_name"), row.get("recipient"), row.get("received_date")
    if not (sender_name or recipient or received):
        return None

    parts = []
    via = sender_name or row.get("platform")
    if via:
        parts.append(f"via {html.escape(str(via))}")
    if recipient:
        parts.append(f"sent to {html.escape(str(recipient))}")
    if received:
        # Stored as UTC ISO 8601; shown in the machine's local time.
        local = pd.Timestamp(received).tz_convert(datetime.now().astimezone().tzinfo)
        parts.append(local.strftime("%b %-d, %-I:%M %p"))

    uid = html.escape(str(row.get("raw_email_uid") or ""))
    return f'<div class="src-line"><span class="uid">uid {uid} ·</span> {" · ".join(parts)}</div>'


def render_pending_section(pending_df: pd.DataFrame, existing_names: set, key_prefix: str) -> None:
    """Render one grouped, expander-per-group 'Pending ...' section.
    Mutates `existing_names` in place so a purchases section and a sales
    section rendered back to back don't suggest the same Excel event name
    twice."""
    if pending_df.empty:
        empty_state("Nothing pending — no scraped transactions awaiting promotion.")
        return

    # pandas turns missing numeric values into NaN, but grouping.py's
    # None-checks rely on real None (NaN is not None in Python, and
    # NaN != NaN, which would silently break the section/row/seat and
    # price-divergence comparisons) — convert before grouping.
    pending_records = (
        pending_df.astype(object).where(pd.notnull(pending_df), None).to_dict("records")
    )
    pending_groups = group_pending_rows(pending_records)

    st.caption(
        f"{len(pending_groups)} group(s) from {len(pending_records)} pending row(s) — "
        "purely informational, nothing here is promoted or matched automatically."
    )

    for i, g in enumerate(pending_groups):
        suggested_name = suggest_event_name(
            g["artist_or_event"], g["event_date"], g["tier"], existing_names
        )
        existing_names.add(suggested_name)  # don't suggest the same name twice in one render

        n_rows = len(g["rows"])
        label = (
            f"{g['artist_or_event']} — {g['event_date']} — {g['tier']} "
            f"({n_rows} row{'s' if n_rows != 1 else ''})"
        )
        if g["flag"]:
            label = f"⚠ {label}"

        wrapper_key = f"{'flag' if g['flag'] else 'grp'}-{key_prefix}-{i}"
        with st.container(key=wrapper_key), st.expander(label):
            if g["flag"]:
                st.markdown(f'<div class="callout">{html.escape(str(g["flag"]))}</div>', unsafe_allow_html=True)

            price_str = (
                f"${g['avg_price_per_ticket']:,.2f}" if g["avg_price_per_ticket"] is not None else "N/A"
            )
            st.markdown(
                f'<div class="chip"><span class="k">Suggested Excel entry</span>'
                f'<span class="v">{html.escape(suggested_name)}</span></div>',
                unsafe_allow_html=True,
            )
            mcol1, mcol2, mcol3 = st.columns(3, gap="small")
            mcol1.metric("Quantity", f"{g['total_quantity']:,.0f}")
            if key_prefix == "sell":
                # Mirrors how Lysted/CrowdVolt describe a sale: gross Sale
                # Total, then the net Payout the seller actually receives.
                payout_str = f"${g['total_payout']:,.2f}" if g["total_payout"] is not None else "—"
                mcol2.metric("Sale Total", f"${g['total_cost']:,.2f}")
                mcol3.metric("Payout", payout_str)
            else:
                mcol2.metric("Total Cost", f"${g['total_cost']:,.2f}")
                mcol3.metric("Price/Ticket", price_str)

            st.dataframe(
                pd.DataFrame(g["rows"])[
                    [
                        "artist_or_event",
                        "platform",
                        "transaction_type",
                        "price_per_ticket",
                        "total_price",
                        "quantity",
                        "section",
                        "row",
                        "seat",
                        "purchase_date",
                        "needs_review",
                        "raw_email_uid",
                    ]
                ].rename(columns={"artist_or_event": "event", "transaction_type": "type"}),
                width="stretch",
                hide_index=True,
                column_config={
                    "price_per_ticket": MONEY_COL,
                    "total_price": MONEY_COL,
                    "needs_review": st.column_config.CheckboxColumn("review"),
                },
            )

            src_lines = [line for line in map(source_line_html, g["rows"]) if line]
            if src_lines:
                st.markdown(f'<div class="src-lines">{"".join(src_lines)}</div>', unsafe_allow_html=True)

            uids = [str(u) for u in g["contributing_uids"]]
            if st.button(
                "Mark as recorded",
                key=f"mark-{key_prefix}-{'-'.join(uids)}",
                help="Use after entering this group in the working sheet under a name the "
                "auto-match can't link. Moves it out of Pending; nothing in the database changes.",
            ):
                mark(uids, note=label)
                st.rerun()


pending_existing_names = set(load_existing_event_names())

pending_buy_df = pending_df[pending_df["transaction_type"] == "buy"]
pending_sell_df = pending_df[pending_df["transaction_type"] == "sell"]


def render_recorded_section(recorded_df: pd.DataFrame, manual_df: pd.DataFrame) -> None:
    """Collapsed lists of scraped rows that already have a line in the sheet:
    auto-matched ones beside the Excel row that records them
    (crosscheck.find_excel_matches), and ones the user marked by hand."""
    if recorded_df.empty and manual_df.empty:
        return
    st.markdown('<div class="group-lbl">Both tabs · handled by hand</div>', unsafe_allow_html=True)
    if not recorded_df.empty:
        render_auto_recorded(recorded_df)
    if not manual_df.empty:
        render_manually_marked(manual_df)


def render_auto_recorded(recorded_df: pd.DataFrame) -> None:
    excel_by_id = excel_df.set_index("id")
    table = recorded_df.assign(excel_id=recorded_df["id"].map(excel_match))
    table = table.assign(
        excel_event=table["excel_id"].map(excel_by_id["artist_or_event"]),
        excel_quantity=table["excel_id"].map(excel_by_id["quantity"]),
        excel_total=table["excel_id"].map(excel_by_id["total_price"]),
    ).sort_values(["excel_id", "id"])
    with st.expander(f"Already recorded in Excel ({len(recorded_df)})"):
        st.caption(
            "Same type and event date, a shared event word, and quantity/total within $1 of an "
            "Excel row — alone or summed with other emails for the same line."
        )
        st.dataframe(
            table[
                [
                    "artist_or_event",
                    "transaction_type",
                    "platform",
                    "quantity",
                    "total_price",
                    "raw_email_uid",
                    "excel_id",
                    "excel_quantity",
                    "excel_total",
                    "excel_event",
                ]
            ].rename(columns={"artist_or_event": "event", "transaction_type": "type", "raw_email_uid": "uid"}),
            width="stretch",
            hide_index=True,
            column_config={
                "total_price": MONEY_COL,
                "excel_id": st.column_config.NumberColumn("excel row", format="%d"),
                "excel_quantity": st.column_config.NumberColumn("excel qty", format="%d"),
                "excel_total": st.column_config.NumberColumn("excel total", format="$%.2f"),
                "excel_event": st.column_config.TextColumn("excel event"),
            },
        )

def render_manually_marked(manual_df: pd.DataFrame) -> None:
    table = manual_df.assign(
        uid=manual_df["raw_email_uid"].astype(str),
        marked_at=manual_df["raw_email_uid"].astype(str).map(lambda u: manual_marks[u]["marked_at"]),
    ).sort_values(["transaction_type", "event_date", "id"])
    with st.expander(f"Manually marked ({len(manual_df)})"):
        st.caption(
            "Marked as recorded from the Pending list — entered in the sheet under a name or "
            "structure the auto-match couldn't link. Stored in manual_marks.csv; commit it so "
            "the pipeline's Pending sheet leaves these out too."
        )
        st.dataframe(
            table[
                [
                    "artist_or_event",
                    "transaction_type",
                    "platform",
                    "event_date",
                    "quantity",
                    "total_price",
                    "uid",
                    "marked_at",
                ]
            ].rename(columns={"artist_or_event": "event", "transaction_type": "type", "marked_at": "marked"}),
            width="stretch",
            hide_index=True,
            column_config={"total_price": MONEY_COL},
        )
        labels = {
            r["uid"]: f"uid {r['uid']} · {r['artist_or_event']} · {r['transaction_type']} · qty {r['quantity']:g}"
            for r in table.to_dict("records")
        }
        chosen = st.multiselect(
            "Unmark (send back to Pending)", list(labels), format_func=labels.get, key="unmark-pick"
        )
        if st.button("Unmark selected", key="unmark-go", disabled=not chosen):
            unmark(chosen)
            st.rerun()


# --- Pending -------------------------------------------------------------------
with st.container(border=True, key="zone-pending"):
    section_header(
        "Pending — not yet in working sheet",
        "03 · inbox",
        count=pending_count,
        sub="Scraped from email, grouped by event / date / tier.",
    )
    tab_buy, tab_sell = st.tabs(
        [f"Purchases · {len(pending_buy_df)}", f"Sales · {len(pending_sell_df)}"]
    )
    with tab_buy:
        render_pending_section(pending_buy_df, pending_existing_names, "buy")
    with tab_sell:
        render_pending_section(pending_sell_df, pending_existing_names, "sell")
    render_recorded_section(recorded_df, manual_df)

# --- Needs review --------------------------------------------------------------
review_df = df[df["needs_review"]]
with st.container(border=True, key="zone-review"):
    section_header("Needs review", "04 · flagged", count=len(review_df), tone=WARN)
    if review_df.empty:
        empty_state("Nothing flagged for review.")
    else:
        st.dataframe(
            review_df[
                [
                    "artist_or_event",
                    "platform",
                    "transaction_type",
                    "review_reason",
                    "total_price",
                    "quantity",
                    "raw_email_uid",
                ]
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "artist_or_event": st.column_config.TextColumn("event"),
                "transaction_type": st.column_config.TextColumn("type"),
                "review_reason": st.column_config.TextColumn("review reason", width="large"),
                "total_price": MONEY_COL,
            },
        )

# --- Analytics -----------------------------------------------------------------
ts_df = excel_df.dropna(subset=["purchase_date"]).copy()
ts_df["purchase_date"] = pd.to_datetime(ts_df["purchase_date"], errors="coerce")
ts_df = ts_df.dropna(subset=["purchase_date"])
ts_df = ts_df[ts_df["transaction_type"].isin(["buy", "sell"])]

matches_df = load_matches()

with st.container(border=True, key="zone-charts"):
    section_header(
        "Spend / revenue over time",
        "05 · analytics",
        tone=MUTED,
        sub="Working sheet only · buys by purchase date, sales (after fees) by date sold, per month. "
        "Months with no activity are skipped.",
    )
    if ts_df.empty:
        st.caption("No dated transactions to chart yet.")
    else:
        window = st.segmented_control(
            "Window", ["Last 13 months", "All time"], default="Last 13 months",
            key="ts_window", label_visibility="collapsed",
        )
        ts_df["month"] = ts_df["purchase_date"].dt.to_period("M").dt.to_timestamp()
        ts_df["amount"] = ts_df["total_price"].where(ts_df["transaction_type"] == "buy", after_fees(ts_df))
        monthly = (
            ts_df.pivot_table(index="month", columns="transaction_type", values="amount", aggfunc="sum", fill_value=0)
            .reindex(columns=["buy", "sell"], fill_value=0)
        )
        # Running cash position over all history, so the 13-month view picks
        # up where earlier months left it rather than restarting at zero.
        monthly["cum_net"] = (monthly["sell"] - monthly["buy"]).cumsum()
        if window != "All time":
            cutoff = (pd.Timestamp.today().to_period("M") - 12).to_timestamp()
            monthly = monthly[monthly.index >= cutoff]

        x = monthly.index.strftime("%Y-%m").tolist()
        full_month = monthly.index.strftime("%B %Y")
        fig_ts = go.Figure()
        fig_ts.add_bar(
            x=x, y=-monthly["buy"], name="Spent", marker_color=SPEND_COLOR,
            customdata=list(zip(full_month, monthly["buy"])),
            hovertemplate="<b>%{customdata[0]}</b><br>Spent $%{customdata[1]:,.0f}<extra></extra>",
        )
        fig_ts.add_bar(
            x=x, y=monthly["sell"], name="Revenue", marker_color=REVENUE_COLOR,
            customdata=full_month,
            hovertemplate="<b>%{customdata}</b><br>Revenue $%{y:,.0f}<extra></extra>",
        )
        fig_ts.add_scatter(
            x=x, y=monthly["cum_net"], yaxis="y2", name="Cumulative net", mode="lines",
            line=dict(color=INK_MARK_COLOR, width=1.5), opacity=0.5,
            customdata=full_month,
            hovertemplate="<b>%{customdata}</b><br>Cumulative net %{y:$,.0f}<extra></extra>",
        )
        # Direct labels on the two extremes only; the tooltip carries the rest.
        for series, sign, anchor in (("buy", -1, "top"), ("sell", 1, "bottom")):
            if monthly[series].max() > 0:
                peak = monthly[series].idxmax()
                fig_ts.add_annotation(
                    x=peak.strftime("%Y-%m"), y=sign * monthly.at[peak, series], text=f"${monthly.at[peak, series]:,.0f}",
                    showarrow=False, yanchor=anchor, yshift=sign * 3, font=dict(size=TYPE_LABEL, color="#cbd5e1"),
                )
        fig_ts.update_layout(barmode="relative")
        style_chart(fig_ts, height=320)
        bars_range, line_range = zero_aligned_ranges(
            (-monthly["buy"].max(), monthly["sell"].max()), (monthly["cum_net"].min(), monthly["cum_net"].max())
        )
        fig_ts.update_layout(
            margin_r=4,
            yaxis=dict(range=bars_range, tickformat="$~s", nticks=6, zeroline=True, zerolinecolor=GRID_COLOR,
                       title_text="Spent (−) / revenue (+)"),
            yaxis2=dict(overlaying="y", side="right", range=line_range, tickformat="$~s", nticks=6, showgrid=False,
                        showline=False, ticks="", tickfont=dict(size=TYPE_AXIS),
                        title=dict(text="Cumulative net (line)", font=dict(size=TYPE_LABEL, color=MUTED))),
        )
        fig_ts.update_xaxes(type="category", tickvals=x, ticktext=month_ticktext(monthly.index), tickangle=0)
        st.plotly_chart(fig_ts, use_container_width=True, config=CHART_CONFIG)

    section_header(
        "Purchase accounts · sale marketplaces",
        "by platform",
        tone=MUTED,
        sub="Bar = dollar volume, dot = average $ per transaction. Case variants (Axs / AXS) are merged; "
        f"categories with fewer than {MIN_POSITIONS} closed positions are grayed out.",
    )
    pcol1, pcol2 = st.columns(2, gap="large")

    with pcol1:
        # 'Ethan', 'Cash App Ethan', 'Paciolan Ethan (…)' are funding sources, not accounts.
        funded = buys["platform"].fillna("").str.contains("ethan", case=False)
        acct_buys = buys[~funded]
        if acct_buys.empty:
            st.caption("No purchase data to chart yet.")
        else:
            accounts = fold_platforms(acct_buys, acct_buys["total_price"], fold={"other"})
            if not matches_df.empty:
                m = matches_df.merge(
                    acct_buys[["id", "platform"]].rename(columns={"id": "buy_id"}), on="buy_id"
                )
                m["key"] = m["platform"].fillna("Unspecified").astype(str).str.strip().str.casefold()
                pnl = m.groupby("key").agg(pairs=("id", "count"), pnl=("net_profit", "sum"), cost=("purchase_cost", "sum"))
                accounts = accounts.join(pnl)
            else:
                accounts = accounts.assign(pairs=0, pnl=0.0, cost=0.0)
            accounts["pairs"] = accounts["pairs"].fillna(0).astype(int)
            accounts["roi"] = accounts["pnl"] / accounts["cost"]

            def _acct_bin(r):
                if r["is_other"] or r["pairs"] < MIN_POSITIONS or pd.isna(r["roi"]):
                    return "none"
                return "good" if r["roi"] > 0.05 else "bad" if r["roi"] < -0.05 else "flat"

            accounts["bin"] = accounts.apply(_acct_bin, axis=1)
            accounts["tick"] = [
                r["platform"] if r["is_other"] or r["bin"] == "none" else f"{r['platform']} {r['roi']:+.0%}"
                for _, r in accounts.iterrows()
            ]
            accounts["detail"] = [
                "" if r["is_other"] else
                f"{r['pairs']} matched pairs" + ("" if r["pairs"] == 0 else f" · P&L {'−' if r['pnl'] < 0 else '+'}${abs(r['pnl']):,.0f} ({r['roi']:+.0%} ROI)")
                for _, r in accounts.iterrows()
            ]
            st.plotly_chart(
                platform_chart(accounts, "Purchase accounts · capital deployed", [
                    ("good", "ROI > +5%", GOOD_COLOR), ("flat", "within ±5%", NEUTRAL_COLOR),
                    ("bad", "ROI < −5%", BAD_COLOR), ("none", f"Other / < {MIN_POSITIONS} pairs", NO_SIGNAL_COLOR),
                ]),
                use_container_width=True, config=CHART_CONFIG,
            )
            st.caption(
                f"P&L from matched pairs, by the account the tickets were bought on. Excludes funding sources "
                f"(Ethan, Cash App Ethan, Paciolan Ethan): ${buys.loc[funded, 'total_price'].sum():,.0f}."
            )

    with pcol2:
        # Loss and Refund rows are write-offs, not sales; Groupme / Zelle are payment channels.
        is_sale = ~sells["platform"].fillna("").str.strip().str.casefold().isin({"loss", "refund"})
        sale_rows = sells[is_sale]
        if sale_rows.empty:
            st.caption("No sale data to chart yet.")
        else:
            markets = fold_platforms(sale_rows, sale_rows["total_price"], fold={"groupme", "zelle"})
            payout = after_fees(sale_rows).groupby(
                sale_rows["platform"].fillna("Unspecified").astype(str).str.strip().str.casefold()
            ).sum()
            markets["payout"] = payout
            markets["fee"] = (markets["volume"] - markets["payout"]) / markets["volume"]

            def _fee_bin(r):
                if r["is_other"] or r["count"] < MIN_POSITIONS or pd.isna(r["fee"]):
                    return "none"
                return "good" if r["fee"] <= 0.03 else "bad" if r["fee"] > 0.10 else "flat"

            markets["bin"] = markets.apply(_fee_bin, axis=1)
            markets["tick"] = [
                r["platform"] if r["is_other"] or r["bin"] == "none" else f"{r['platform']} {r['fee']:.0%}"
                for _, r in markets.iterrows()
            ]
            markets["detail"] = [
                "" if r["is_other"] else f"fee {r['fee']:.1%} · payout ${r['payout']:,.0f}"
                for _, r in markets.iterrows()
            ]
            st.plotly_chart(
                platform_chart(markets, "Sale marketplaces · revenue in", [
                    ("good", "fee ≤ 3%", GOOD_COLOR), ("flat", "fee 3–10%", NEUTRAL_COLOR),
                    ("bad", "fee > 10%", BAD_COLOR), ("none", f"Other / < {MIN_POSITIONS} sales", NO_SIGNAL_COLOR),
                ]),
                use_container_width=True, config=CHART_CONFIG,
            )
            st.caption(
                "Bar = gross sales; fee rate = (gross − payout) / gross. Excludes Loss and Refund rows. "
                "StubHub's gross in the sheet is mostly payout × 1.15, so its rate reflects that formula."
            )

# --- Matched pairs -------------------------------------------------------------
if not matches_df.empty:
    with st.container(border=True, key="zone-matches"):
        profit_col = next(
            (c for c in ("net_profit", "profit", "realized_profit") if c in matches_df.columns), None
        )
        section_header("Matched buy/sell pairs", "06 · realized", count=len(matches_df), tone=MUTED)
        if profit_col:
            matched_profit = matches_df[profit_col].sum()
            st.markdown(
                stat_grid(
                    [
                        stat_card(
                            "Total realized profit (matched pairs)",
                            money_html(matched_profit, signed=True),
                            tone="pos" if matched_profit >= 0 else "neg",
                        )
                    ],
                    3,
                ),
                unsafe_allow_html=True,
            )

        def _pl_color(v):
            if pd.isna(v) or v == 0:
                return ""
            return f"color: {POS}" if v > 0 else f"color: {NEG}"

        money_cols = [c for c in ("purchase_cost", "gross_sale", "net_profit", "profit", "realized_profit") if c in matches_df.columns]
        pl_cols = [c for c in (profit_col, "roi") if c and c in matches_df.columns]
        fmt = {c: (lambda v: f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}") for c in money_cols}
        if "roi" in matches_df.columns:
            fmt["roi"] = "{:.1%}"
        st.dataframe(
            matches_df.style.format(fmt, na_rep="—").map(_pl_color, subset=pl_cols),
            width="stretch",
            hide_index=True,
        )

# --- All transactions ----------------------------------------------------------
display_df = df.copy()
display_df["platform"] = display_df["platform"].fillna("Unknown")
display_df["transaction_type"] = display_df["transaction_type"].fillna("Unknown")

platforms = sorted(display_df["platform"].unique().tolist())
types = sorted(display_df["transaction_type"].unique().tolist())

with st.container(border=True, key="zone-all"):
    section_header("All transactions", "07 · ledger", count=len(display_df), tone=MUTED)
    fcol1, fcol2 = st.columns(2, gap="small")
    platform_filter = fcol1.multiselect("Filter by platform", platforms, default=platforms)
    type_filter = fcol2.multiselect("Filter by type", types, default=types)

    filtered = display_df[
        display_df["platform"].isin(platform_filter)
        & display_df["transaction_type"].isin(type_filter)
    ]

    st.dataframe(filtered, width="stretch")

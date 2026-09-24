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
import plotly.express as px
import streamlit as st

from db import DB_PATH
from grouping import group_pending_rows, read_existing_event_names, suggest_event_name

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
BUY_COLOR = ACCENT
SELL_COLOR = "#818cf8"

st.markdown(
    f"""
<style>
:root {{
  --accent: {ACCENT}; --warn: {WARN}; --pos: {POS}; --neg: {NEG};
  --muted: {MUTED}; --border: {BORDER}; --card: #111a2e; --card-2: #0f1729;
  --mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
}}
.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px; }}
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {{ gap: 1.5rem; }}
[class*="st-key-grp-"], [class*="st-key-flag-"] {{ margin-bottom: -.55rem; }}
footer, #MainMenu {{ visibility: hidden; }}

/* Header band */
.hdr {{ display: flex; align-items: flex-end; justify-content: space-between;
  border-bottom: 1px solid var(--border); padding-bottom: .9rem; }}
.hdr h1 {{ font-size: 1.65rem; font-weight: 700; letter-spacing: -.02em; margin: 0; padding: 0; }}
.hdr h1 .tick {{ color: var(--accent); }}
.hdr .meta {{ color: var(--muted); font-family: var(--mono); font-size: .78rem; }}

/* Section heads */
.sec {{ display: flex; align-items: center; gap: .6rem; margin: 0 0 .15rem 0; }}
.sec .eyebrow {{ font-family: var(--mono); font-size: .68rem; letter-spacing: .14em;
  text-transform: uppercase; color: var(--tone); }}
.sec .title {{ font-size: 1.12rem; font-weight: 650; color: #e2e8f0; }}
.pill {{ font-family: var(--mono); font-size: .72rem; padding: .1rem .5rem; border-radius: 999px;
  background: color-mix(in srgb, var(--tone) 16%, transparent); color: var(--tone);
  border: 1px solid color-mix(in srgb, var(--tone) 40%, transparent); }}
.sec-sub {{ color: var(--muted); font-size: .8rem; margin: 0 0 .4rem 0; }}

/* Zones = keyed bordered containers, each with a colored top rule */
[class*="st-key-zone-"] {{ background: var(--card-2); border-top: 2px solid var(--zone, var(--border)) !important; }}
.st-key-zone-overview {{ --zone: var(--accent); }}
.st-key-zone-pending  {{ --zone: var(--accent); }}
.st-key-zone-review   {{ --zone: var(--warn); }}
.st-key-zone-charts, .st-key-zone-matches, .st-key-zone-all {{ --zone: #334155; }}

/* Stat cards */
.stats {{ display: grid; gap: .6rem; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: .6rem;
  padding: .7rem .9rem .75rem; min-width: 0; }}
.card .lbl {{ font-family: var(--mono); font-size: .66rem; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted); }}
.card .val {{ font-family: var(--mono); font-size: 1.55rem; font-weight: 600;
  font-variant-numeric: tabular-nums; margin-top: .15rem; white-space: nowrap; }}
.card .sub {{ color: var(--muted); font-size: .72rem; margin-top: .1rem; }}
.card.warn {{ border-color: color-mix(in srgb, var(--warn) 45%, transparent);
  background: color-mix(in srgb, var(--warn) 7%, var(--card)); }}
.card.warn .val, .card.warn .lbl {{ color: var(--warn); }}
.card.pos {{ border-color: color-mix(in srgb, var(--pos) 40%, transparent); }}
.card.neg {{ border-color: color-mix(in srgb, var(--neg) 40%, transparent); }}
.group-lbl {{ font-family: var(--mono); font-size: .66rem; letter-spacing: .14em;
  text-transform: uppercase; color: var(--muted); margin: .1rem 0 .35rem; }}
.pos {{ color: var(--pos); }} .neg {{ color: var(--neg); }} .na {{ color: var(--muted); }}

/* Banner + callouts */
.banner {{ display: flex; gap: .7rem; align-items: center; padding: .6rem .9rem; border-radius: .6rem;
  border: 1px solid color-mix(in srgb, var(--warn) 40%, transparent);
  background: color-mix(in srgb, var(--warn) 8%, transparent); color: #fde68a; font-size: .86rem; }}
.banner b {{ color: var(--warn); font-family: var(--mono); }}
.banner code {{ background: rgba(0,0,0,.3); color: #fde68a; }}
.callout {{ border-left: 3px solid var(--warn); background: color-mix(in srgb, var(--warn) 8%, transparent);
  color: #fde68a; padding: .45rem .75rem; border-radius: 0 .4rem .4rem 0; font-size: .84rem; margin-bottom: .4rem; }}
.chip {{ display: inline-flex; gap: .45rem; align-items: center; font-size: .82rem; margin-bottom: .3rem; }}
.chip .k {{ font-family: var(--mono); font-size: .64rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); }}
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
[data-testid="stMetricLabel"] p {{ font-family: var(--mono); font-size: .66rem; letter-spacing: .1em;
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


def stat_card(label: str, value_html: str, sub: str | None = None, tone: str | None = None) -> str:
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return (
        f'<div class="card {tone or ""}"><div class="lbl">{label}</div>'
        f'<div class="val">{value_html}</div>{sub_html}</div>'
    )


def stat_grid(cards: list[str], cols: int) -> str:
    return f'<div class="stats" style="grid-template-columns:repeat({cols},minmax(0,1fr))">{"".join(cards)}</div>'


def empty_state(msg: str) -> None:
    st.markdown(f'<div class="empty">{msg}</div>', unsafe_allow_html=True)


def style_chart(fig, height: int = 300):
    has_title = bool(fig.layout.title.text)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="JetBrains Mono, SF Mono, Menlo, monospace", size=11, color=MUTED),
        margin=dict(l=8, r=8, t=36 if has_title else 8, b=8),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, title_text=""),
        bargap=0.25,
        height=height,
    )
    if has_title:
        fig.update_layout(title_font=dict(size=12, color="#cbd5e1"))
    fig.update_xaxes(showgrid=False, linecolor=BORDER, title_text="")
    fig.update_yaxes(gridcolor=BORDER, zeroline=False, title_font=dict(size=10))
    return fig


def style_platform_chart(fig, n_bars: int):
    style_chart(fig, height=max(260, 22 * n_bars + 60))
    fig.update_yaxes(categoryorder="total ascending", gridcolor="rgba(0,0,0,0)", title_text="", tickfont=dict(size=10))
    fig.update_xaxes(gridcolor=BORDER, showgrid=True)
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
pending_count = int(((df["source"] == "email") & (~df["promoted"])).sum())

buys = excel_df[excel_df["transaction_type"] == "buy"]
sells = excel_df[excel_df["transaction_type"] == "sell"]

total_bought = buys["quantity"].sum()
total_sold = sells["quantity"].sum()
total_spent = buys["total_price"].sum()
total_revenue = sells["total_price"].sum()
has_both_sides = buys["total_price"].notna().any() and sells["total_price"].notna().any()
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
        "(source='email', not yet promoted) — excluded from the metrics below. "
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
                    stat_card("Total revenue", money_html(total_revenue if pd.notna(total_revenue) else 0.0)),
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


pending_existing_names = set(load_existing_event_names())

pending_buy_df = df[(df["source"] == "email") & (~df["promoted"]) & (df["transaction_type"] == "buy")]
pending_sell_df = df[(df["source"] == "email") & (~df["promoted"]) & (df["transaction_type"] == "sell")]

# --- Pending -------------------------------------------------------------------
with st.container(border=True, key="zone-pending"):
    section_header(
        "Pending — not yet in working sheet",
        "02 · inbox",
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

# --- Needs review --------------------------------------------------------------
review_df = df[df["needs_review"]]
with st.container(border=True, key="zone-review"):
    section_header("Needs review", "03 · flagged", count=len(review_df), tone=WARN)
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

platform_df = excel_df.dropna(subset=["platform"])

with st.container(border=True, key="zone-charts"):
    section_header("Spend / revenue over time", "04 · analytics", tone=MUTED)
    if ts_df.empty:
        st.caption("No dated transactions to chart yet.")
    else:
        ts_df["month"] = ts_df["purchase_date"].dt.to_period("M").dt.to_timestamp()
        monthly = (
            ts_df.groupby(["month", "transaction_type"])["total_price"]
            .sum()
            .reset_index()
        )
        fig_ts = px.bar(
            monthly,
            x="month",
            y="total_price",
            color="transaction_type",
            barmode="group",
            labels={"month": "Month", "total_price": "Amount ($)", "transaction_type": "Type"},
            color_discrete_map={"buy": BUY_COLOR, "sell": SELL_COLOR},
        )
        st.plotly_chart(style_chart(fig_ts), use_container_width=True)

    section_header("Breakdown by platform", "by platform", tone=MUTED)
    if platform_df.empty:
        st.caption("No platform data to chart yet.")
    else:
        platform_summary = (
            platform_df.groupby("platform")
            .agg(transaction_count=("id", "count"), total_volume=("total_price", "sum"))
            .reset_index()
        )
        pcol1, pcol2 = st.columns(2, gap="medium")
        with pcol1:
            fig_count = px.bar(
                platform_summary,
                x="transaction_count",
                y="platform",
                orientation="h",
                labels={"platform": "Platform", "transaction_count": "Transactions"},
                title="Transaction count",
                color_discrete_sequence=[ACCENT],
            )
            st.plotly_chart(style_platform_chart(fig_count, len(platform_summary)), use_container_width=True)
        with pcol2:
            fig_volume = px.bar(
                platform_summary,
                x="total_volume",
                y="platform",
                orientation="h",
                labels={"platform": "Platform", "total_volume": "Volume ($)"},
                title="Dollar volume",
                color_discrete_sequence=[SELL_COLOR],
            )
            st.plotly_chart(style_platform_chart(fig_volume, len(platform_summary)), use_container_width=True)

# --- Matched pairs -------------------------------------------------------------
matches_df = load_matches()
if not matches_df.empty:
    with st.container(border=True, key="zone-matches"):
        profit_col = next(
            (c for c in ("net_profit", "profit", "realized_profit") if c in matches_df.columns), None
        )
        section_header("Matched buy/sell pairs", "05 · realized", count=len(matches_df), tone=MUTED)
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
    section_header("All transactions", "06 · ledger", count=len(display_df), tone=MUTED)
    fcol1, fcol2 = st.columns(2, gap="small")
    platform_filter = fcol1.multiselect("Filter by platform", platforms, default=platforms)
    type_filter = fcol2.multiselect("Filter by type", types, default=types)

    filtered = display_df[
        display_df["platform"].isin(platform_filter)
        & display_df["transaction_type"].isin(type_filter)
    ]

    st.dataframe(filtered, width="stretch")

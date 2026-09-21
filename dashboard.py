"""
Local dashboard for ticket-transaction bookkeeping.

Reads directly from transactions.db on every run (no separate export step).

Run with:
    streamlit run dashboard.py
"""

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from db import DB_PATH
from grouping import group_pending_rows, read_existing_event_names, suggest_event_name

st.set_page_config(page_title="Ticket Transactions", layout="wide")


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


st.title("Ticket Transactions Dashboard")

df = load_transactions()

if df.empty:
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

st.subheader("Overview")
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Tickets bought", f"{total_bought:,.0f}" if pd.notna(total_bought) else "0")
col2.metric("Tickets sold", f"{total_sold:,.0f}" if pd.notna(total_sold) else "0")
col3.metric("Total spent", f"${total_spent:,.2f}" if pd.notna(total_spent) else "$0.00")
col4.metric("Total revenue", f"${total_revenue:,.2f}" if pd.notna(total_revenue) else "$0.00")
col5.metric(
    "Realized profit (naive)",
    f"${(total_revenue - total_spent):,.2f}" if has_both_sides else "N/A",
    help="Revenue minus spend. Doesn't account for unsold/unmatched inventory — see matched-pairs profit below if available.",
)
col6.metric("Needs review", review_count)

if pending_count:
    st.info(
        f"{pending_count} scraped transaction(s) pending review (source='email', not yet "
        "promoted) — excluded from the metrics above. Run `python review_pending.py` to list them."
    )

def render_pending_section(pending_df: pd.DataFrame, existing_names: set) -> None:
    """Render one grouped, expander-per-group 'Pending ...' section.
    Mutates `existing_names` in place so a purchases section and a sales
    section rendered back to back don't suggest the same Excel event name
    twice."""
    if pending_df.empty:
        st.success("Nothing pending — no scraped transactions awaiting promotion.")
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

    for g in pending_groups:
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
            label += "  [FLAGGED]"

        with st.expander(label):
            if g["flag"]:
                st.warning(g["flag"])

            price_str = (
                f"${g['avg_price_per_ticket']:,.2f}" if g["avg_price_per_ticket"] is not None else "N/A"
            )
            st.markdown(f"**Suggested Excel entry** — Event: `{suggested_name}`")
            mcol1, mcol2, mcol3 = st.columns(3)
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
            )


pending_existing_names = set(load_existing_event_names())

st.subheader("Pending purchases (not yet in working sheet)")
pending_buy_df = df[(df["source"] == "email") & (~df["promoted"]) & (df["transaction_type"] == "buy")]
render_pending_section(pending_buy_df, pending_existing_names)

st.subheader("Pending sales (not yet in working sheet)")
pending_sell_df = df[(df["source"] == "email") & (~df["promoted"]) & (df["transaction_type"] == "sell")]
render_pending_section(pending_sell_df, pending_existing_names)

st.subheader("Needs review")
review_df = df[df["needs_review"]]
if review_df.empty:
    st.success("Nothing flagged for review.")
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
    )

st.subheader("Spend / revenue over time")
ts_df = excel_df.dropna(subset=["purchase_date"]).copy()
ts_df["purchase_date"] = pd.to_datetime(ts_df["purchase_date"], errors="coerce")
ts_df = ts_df.dropna(subset=["purchase_date"])
ts_df = ts_df[ts_df["transaction_type"].isin(["buy", "sell"])]

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
    )
    st.plotly_chart(fig_ts, use_container_width=True)

st.subheader("Breakdown by platform")
platform_df = excel_df.dropna(subset=["platform"])
if platform_df.empty:
    st.caption("No platform data to chart yet.")
else:
    platform_summary = (
        platform_df.groupby("platform")
        .agg(transaction_count=("id", "count"), total_volume=("total_price", "sum"))
        .reset_index()
    )
    pcol1, pcol2 = st.columns(2)
    with pcol1:
        fig_count = px.bar(
            platform_summary,
            x="platform",
            y="transaction_count",
            labels={"platform": "Platform", "transaction_count": "Transactions"},
            title="Transaction count",
        )
        st.plotly_chart(fig_count, use_container_width=True)
    with pcol2:
        fig_volume = px.bar(
            platform_summary,
            x="platform",
            y="total_volume",
            labels={"platform": "Platform", "total_volume": "Volume ($)"},
            title="Dollar volume",
        )
        st.plotly_chart(fig_volume, use_container_width=True)

matches_df = load_matches()
if not matches_df.empty:
    st.subheader("Matched buy/sell pairs")
    st.dataframe(matches_df, width="stretch")

    profit_col = next(
        (c for c in ("net_profit", "profit", "realized_profit") if c in matches_df.columns), None
    )
    if profit_col:
        st.metric("Total realized profit (matched pairs)", f"${matches_df[profit_col].sum():,.2f}")

st.subheader("All transactions")
display_df = df.copy()
display_df["platform"] = display_df["platform"].fillna("Unknown")
display_df["transaction_type"] = display_df["transaction_type"].fillna("Unknown")

platforms = sorted(display_df["platform"].unique().tolist())
types = sorted(display_df["transaction_type"].unique().tolist())

fcol1, fcol2 = st.columns(2)
platform_filter = fcol1.multiselect("Filter by platform", platforms, default=platforms)
type_filter = fcol2.multiselect("Filter by type", types, default=types)

filtered = display_df[
    display_df["platform"].isin(platform_filter)
    & display_df["transaction_type"].isin(type_filter)
]

st.dataframe(filtered, width="stretch")

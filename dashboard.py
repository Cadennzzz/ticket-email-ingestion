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


st.title("Ticket Transactions Dashboard")

df = load_transactions()

if df.empty:
    st.info("No data yet — run `python ingest.py` to scan your inbox and populate transactions.db.")
    st.stop()

df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
df["total_price"] = pd.to_numeric(df["total_price"], errors="coerce")
df["needs_review"] = df["needs_review"].astype(bool)

buys = df[df["transaction_type"] == "buy"]
sells = df[df["transaction_type"] == "sell"]

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
        use_container_width=True,
    )

st.subheader("Spend / revenue over time")
ts_df = df.dropna(subset=["purchase_date"]).copy()
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
platform_df = df.dropna(subset=["platform"])
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
    st.dataframe(matches_df, use_container_width=True)

    profit_col = next((c for c in ("profit", "realized_profit") if c in matches_df.columns), None)
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

st.dataframe(filtered, use_container_width=True)

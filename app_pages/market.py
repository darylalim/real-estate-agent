"""Market dashboard over the listings provider.

No model runs on this page and no API key is needed — it reads the provider
directly. The headline statistics come from the agent's own `market_statistics`
tool rather than being recomputed here; see `ui/market_data.py` for why that
matters.
"""

from collections import Counter

import pandas as pd
import streamlit as st

from ui.market_data import (
    dataset_choices,
    listings_frame,
    market_snapshot,
    state_for_city,
)

_STATUS_CHOICES = {"Active": "active", "Sold": "sold", "Pending": "pending", "All": None}

# Width of one bar in the price histogram. Native charts want pre-binned data;
# Altair would bin for us, but its fluent builder is opaque to ty and this repo
# treats a ty warning as a failure.
_PRICE_BAND = 50_000

st.title("Market")
st.caption(
    "Supply, pricing and absorption straight from the listings provider — the "
    "same numbers the market-analyst specialist is given."
)


def _money(value: float | None, *, cents: bool = False) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}" if cents else f"${value:,.0f}"


def _plain(value: float | None, suffix: str = "") -> str:
    return "—" if value is None else f"{value:,.0f}{suffix}"


cities, property_types = dataset_choices()

with st.sidebar:
    st.subheader("Filters")
    city = st.selectbox("City", cities, key="market_city")
    property_type_label = st.selectbox(
        "Property type", ["All", *property_types], key="market_type"
    )
    status_label = st.segmented_control(
        "Status", list(_STATUS_CHOICES), default="Active", key="market_status"
    )
    months_back = st.slider(
        "Closed-sales window (months)",
        min_value=3,
        max_value=24,
        value=12,
        key="market_months",
        help="Filters the sales themselves, not just the divisor — which is what "
        "makes months-of-inventory move for a real reason.",
    )

property_type = None if property_type_label == "All" else property_type_label
status = _STATUS_CHOICES.get(status_label or "Active")
state = state_for_city(city)

snapshot = market_snapshot(city, state, property_type, months_back)
active = snapshot["active_inventory"]
closed = snapshot["closed_sales"]
months_of_inventory = snapshot["months_of_inventory"]

# The tool ships its own reading of this number; mirror its thresholds rather
# than inventing new ones.
if months_of_inventory is None:
    reading = "No closed sales in the window"
elif months_of_inventory < 4:
    reading = "Seller's market"
elif months_of_inventory > 6:
    reading = "Buyer's market"
else:
    reading = "Balanced market"

# Asking vs achieved: the gap is the negotiation signal, so show it as a delta
# rather than making the reader subtract two medians.
price_delta = None
if active["median_price"] is not None and closed["median_price"] is not None:
    price_delta = active["median_price"] - closed["median_price"]

with st.container(horizontal=True):
    st.metric("Active listings", _plain(active["count"]), border=True)
    st.metric(
        "Median asking price",
        _money(active["median_price"]),
        delta=None if price_delta is None else f"{_money(price_delta)} vs closed",
        border=True,
    )
    st.metric("Median $/sqft", _money(active["median_price_per_sqft"], cents=True), border=True)
    st.metric("Median days on market", _plain(active["median_days_on_market"]), border=True)
    st.metric(
        "Months of inventory",
        "—" if months_of_inventory is None else f"{months_of_inventory:g}",
        delta=reading,
        delta_color="off",
        border=True,
    )

st.caption(
    f"Closed sales in the last {months_back} months: {closed['count']}"
    + (
        f" · median {_money(closed['median_price'])}"
        if closed["median_price"] is not None
        else ""
    )
)

frame = listings_frame(city, state, property_type, status)

if frame.empty:
    st.info(
        f"No {(status or 'matching')} listings in {city} for that filter combination.",
        icon=":material/search_off:",
    )
    st.stop()

left, right = st.columns(2)

with left, st.container(border=True):
    st.subheader("Price distribution")
    banded = Counter(
        int(price // _PRICE_BAND) * _PRICE_BAND for price in frame["price"].tolist()
    )
    bands = sorted(banded)
    st.bar_chart(
        pd.DataFrame(
            {"band": bands, "listings": [banded[band] for band in bands]}
        ),
        x="band",
        y="listings",
        x_label=f"Price band (${_PRICE_BAND // 1000}k wide, lower bound)",
        y_label="Listings",
        height=280,
    )

with right, st.container(border=True):
    st.subheader("Price against size")
    st.scatter_chart(
        frame,
        x="sqft",
        y="price",
        color="property_type",
        x_label="Living area (sqft)",
        y_label="Price ($)",
        height=280,
    )

with st.container(border=True):
    st.subheader("Where they are")
    st.map(frame, latitude="latitude", longitude="longitude", size=40)

with st.container(border=True):
    st.subheader(f"{len(frame)} listings")
    st.dataframe(
        frame,
        hide_index=True,
        column_config={
            "listing_id": st.column_config.TextColumn("ID", pinned=True),
            "address": st.column_config.TextColumn("Address", width="medium"),
            "price": st.column_config.NumberColumn("Price", format="dollar", step=1),
            "sold_price": st.column_config.NumberColumn("Sold", format="dollar", step=1),
            "price_per_sqft": st.column_config.NumberColumn("$/sqft", format="dollar"),
            "sqft": st.column_config.NumberColumn("Sqft", format="localized"),
            "lot_sqft": st.column_config.NumberColumn("Lot sqft", format="localized"),
            "days_on_market": st.column_config.NumberColumn("DOM"),
            "sold_date": st.column_config.DatetimeColumn("Sold on", format="MMM DD, YYYY"),
            "hoa_monthly": st.column_config.NumberColumn("HOA", format="dollar", step=1),
            "year_built": st.column_config.NumberColumn("Built", format="%d"),
            # Rendered on the map above; noise in the table.
            "latitude": None,
            "longitude": None,
            "description": None,
        },
    )

st.caption(snapshot["interpretation_hint"])

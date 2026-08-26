"""Market dashboard over the listings provider.

No model runs on this page and no API key is needed — it reads the provider
directly. The headline statistics come from the agent's own `market_statistics`
tool rather than being recomputed here; see `ui/market_data.py` for why that
matters.
"""

from collections import Counter

import pandas as pd
import streamlit as st

from real_estate_agent.tools.market import BUYERS_MARKET_MONTHS, SELLERS_MARKET_MONTHS
from ui.market_data import (
    dataset_choices,
    listings_frame,
    market_snapshot,
)

_STATUS_CHOICES = {"Active": "active", "Sold": "sold", "Pending": "pending", "All": None}

# Width of one bar in the price histogram. Native charts want pre-binned data;
# Altair would bin for us, but its fluent builder is opaque to ty and this repo
# treats a ty warning as a failure.
_PRICE_BAND = 50_000

# Dropped before display, not hidden with `column_config={name: None}`. That
# only hides a column in the browser — every value is still serialised into the
# page payload, so a listing `description` nobody can see still ships on every
# row. Latitude and longitude are already on the map above, and
# `effective_price` is a chart intermediate that duplicates `price`/`sold_price`.
_NOT_IN_THE_TABLE = ["latitude", "longitude", "description", "effective_price"]

# `column_config` does **not** reorder anything; only `column_order` does, and
# without it the browser shows the frame's own order, which is `Listing`'s field
# order — putting the three currency columns 6th, 15th and 18th of 18, so
# comparing an asking price against what the place actually fetched needs a
# horizontal scroll.
#
# Omitting a column here is **not** the same as dropping it above: it is hidden,
# the reader can restore it from the column-visibility menu, and its values ship
# either way — which also means they still reach the toolbar's CSV export.
# `city` and `state` take that route deliberately. They are constant down every
# row (the frame is filtered to one city) and so are noise in the two leftmost
# columns, but a downloaded CSV with no market recorded on it is worse than a
# repeated value, and the page names the city nowhere else. Withholding is
# `_NOT_IN_THE_TABLE` above, for payload that must not reach the browser at all.
_HIDDEN_BUT_EXPORTED = ("city", "state")
_TABLE_ORDER = (
    "listing_id",
    "address",
    "zip_code",
    "property_type",
    "status",
    "beds",
    "baths",
    "sqft",
    "lot_sqft",
    "year_built",
    "price",
    "sold_price",
    "price_per_sqft",
    "hoa_monthly",
    "days_on_market",
    "sold_date",
)

# The map draws one dot per listing and, uncoloured, answers "where" and nothing
# else — so under "All" a closed comp and a standing listing are the same mark.
# One literal, holding the status vocabulary, its dot colour and the word the
# legend uses for that colour, so the three cannot drift apart.
#
# The hexes are the theme's first three `chartCategoricalColors`, reused rather
# than invented: that triad is the one measured against both backgrounds *and*
# for colour-vision deficiency (worst pairwise CIELAB distance 63 under
# deuteranopia). `test_the_map_status_colours_match_the_chart_palette` ties them
# to `.streamlit/config.toml` rather than trusting the copy. The scatter panel
# spends the same three on `property_type`, which is why the map carries its own
# legend below.
_STATUS_MARKS = {
    "active": ("#3B82F6", "Blue"),
    "sold": ("#EA580C", "Orange"),
    "pending": ("#0D9488", "Teal"),
}
_UNKNOWN_STATUS_COLOUR = "#64748B"

st.title("Market")
st.caption(
    "Supply, pricing and absorption straight from the listings provider — the "
    "same numbers the market-analyst specialist is given."
)


def _money(value: float | None, *, cents: bool = False) -> str:
    """Currency, with any minus sign *outside* the symbol.

    `st.metric` classifies a delta's direction with
    `str(delta).startswith("-")`, so "$-36,500" reads as an increase and draws a
    green up-arrow — the exact inversion of what a below-asking market means.
    "-$36,500" is read correctly.
    """
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}" if cents else f"{sign}${abs(value):,.0f}"


def _plain(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


cities, property_types, states = dataset_choices()

with st.sidebar:
    st.subheader("Filters")
    city = st.selectbox("City", cities, key="market_city")
    property_type_label = st.selectbox(
        "Property type", ["All", *property_types], key="market_type"
    )
    # `required=True`, because the 1.62 default lets a second click *deselect*
    # the chosen segment. That returned None, the fallback below mapped it back
    # to "active", and the page then filtered, counted and captioned Active
    # listings while the control displayed no selection at all — nothing on
    # screen saying which filter was in force. The fallback stays as defence;
    # with `required` set there is no longer a click that reaches it.
    status_label = st.segmented_control(
        "Status",
        list(_STATUS_CHOICES),
        default="Active",
        required=True,
        key="market_status",
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
state = states.get(city)

snapshot = market_snapshot(city, state, property_type, months_back)
active = snapshot["active_inventory"]
closed = snapshot["closed_sales"]
months_of_inventory = snapshot["months_of_inventory"]

# Imported, not re-typed. The tool ships its own reading of this number in
# `interpretation_hint`, which is printed verbatim at the bottom of this page —
# a second hardcoded 4/6 here would let the badge and the caption contradict
# each other on the same screen after a one-line change in tools/market.py.
if months_of_inventory is None:
    reading = "No closed sales in the window"
elif months_of_inventory < SELLERS_MARKET_MONTHS:
    reading = "Seller's market"
elif months_of_inventory > BUYERS_MARKET_MONTHS:
    reading = "Buyer's market"
else:
    reading = "Balanced market"

# Asking vs achieved: the gap is the negotiation signal, so show it as a delta
# rather than making the reader subtract two medians.
price_delta = None
if active["median_price"] is not None and closed["median_price"] is not None:
    price_delta = active["median_price"] - closed["median_price"]
# `_money` renders whole dollars, so both the display and the arrow are taken
# from the rounded figure — one number, rather than two that happen to round the
# same way. Classified off the raw value, a delta of 0.5 rendered "$0" and was
# still truthy, drawing a green up-arrow beside it: the symptom the metric below
# describes closing, surviving in the sub-dollar case. Unreachable on the mock,
# reachable on a real feed, where a median lands on .5 whenever the sample is
# even-sized.
whole_dollar_delta = None if price_delta is None else round(price_delta)

with st.container(horizontal=True):
    st.metric("Active listings", _plain(active["count"]), border=True)
    st.metric(
        "Median asking price",
        _money(active["median_price"]),
        delta=None if whole_dollar_delta is None else _money(whole_dollar_delta),
        # `st.metric`'s zero rule — "if the delta is zero, no arrow is shown and
        # the delta is gray" — is a check on a *number*. This delta is a string,
        # and "$0" neither equals zero nor starts with "-", so a market whose
        # asking and closed medians agree fell through to "otherwise, up and
        # green" and reported a rising market. Classify off the number and let
        # `_money` keep owning the formatting.
        delta_arrow="auto" if whole_dollar_delta else "off",
        delta_color="normal" if whole_dollar_delta else "off",
        # Its own parameter rather than concatenated into the delta: appended,
        # the qualifier rendered at delta size in the delta's red or green, as
        # though "vs closed" were part of the signal. `delta_description` is
        # muted and caption-sized.
        delta_description=None if price_delta is None else "vs closed",
        border=True,
    )
    st.metric("Median $/sqft", _money(active["median_price_per_sqft"], cents=True), border=True)
    st.metric("Median days on market", _plain(active["median_days_on_market"]), border=True)
    st.metric(
        "Months of inventory",
        "—" if months_of_inventory is None else f"{months_of_inventory:g}",
        delta=reading,
        # `delta_color="off"` greys the delta; it does not remove the arrow.
        # `reading` is prose — never empty, never numeric zero, never starting
        # with "-" — so every value of it, "Buyer's market" and "No closed sales
        # in the window" included, drew a grey *up*-arrow next to a reading with
        # no direction at all. `delta_arrow="off"` is the parameter that means
        # what the colour one was being asked to.
        delta_arrow="off",
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
        int(price // _PRICE_BAND) * _PRICE_BAND
        for price in frame["effective_price"].tolist()
    )
    # Every band across the range, including the empty ones. `Counter` holds
    # only what it saw, and `st.bar_chart` treats even a numeric x as ordinal —
    # so a band with no listings would not leave a gap, it would vanish, and its
    # neighbours would close up. A bimodal market then reads as unimodal.
    bands = list(range(min(banded), max(banded) + _PRICE_BAND, _PRICE_BAND))
    st.bar_chart(
        pd.DataFrame({"band": bands, "listings": [banded.get(b, 0) for b in bands]}),
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
        y="effective_price",
        color="property_type",
        x_label="Living area (sqft)",
        y_label="Price ($) — sold price where there is one",
        height=280,
    )

with st.container(border=True):
    st.subheader("Where they are")
    # `.str.lower()` because `ListingsProvider.search` documents its own matching
    # as case-insensitive, so a real adapter may store "Active". Filtering would
    # still work — lowercase goes *into* `search` — while every lookup here
    # missed, `fillna` painted the whole map the unknown grey, and the legend
    # went on naming three colours that were not on screen. Nothing raises.
    #
    # `.assign`, not an assignment: `frame` is handed to the table below, and a
    # colour column added in place would arrive there as a stray hex string.
    dots = {status: colour for status, (colour, _) in _STATUS_MARKS.items()}
    st.map(
        frame.assign(
            _dot=frame["status"].str.lower().map(dots).fillna(_UNKNOWN_STATUS_COLOUR)
        ),
        latitude="latitude",
        longitude="longitude",
        color="_dot",
        size=40,
    )
    # `st.map` draws no legend of its own, and this one names only the statuses
    # actually drawn — static, it advertised "Teal: pending" under a Sold-only
    # filter, describing marks that are not there. The colours are named in
    # words rather than drawn as swatches: Streamlit's `:blue[…]` markdown
    # resolves to the theme's *semantic* `blueColor`, not to the chart palette
    # these dots come from, so a swatch would be a near-match that is simply
    # wrong for the teal one. Words cannot drift out of step with a hex.
    on_screen = set(frame["status"].str.lower())
    legend = " · ".join(
        f"{name}: {status}"
        for status, (_, name) in _STATUS_MARKS.items()
        if status in on_screen
    )
    if legend:
        st.caption(legend)

with st.container(border=True):
    st.subheader(f"{len(frame)} listings")
    st.dataframe(
        frame.drop(columns=_NOT_IN_THE_TABLE),
        hide_index=True,
        column_order=_TABLE_ORDER,
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
        },
    )

st.caption(snapshot["interpretation_hint"])

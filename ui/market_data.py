"""Data for the market dashboard.

One deliberate choice runs through this module: **the statistics come from the
agent's own `market_statistics` tool**, parsed out of the JSON it returns,
rather than being recomputed here against the same provider.

That is not indirection for its own sake. Months-of-inventory was a shipped
defect once -- the window divided the numerator without filtering it, so the
same 16 sales read as either a balanced or an extreme buyer's market depending
on a parameter that changed nothing. A second implementation is a second chance
to get it wrong, and a dashboard that quietly disagrees with what the analyst
was told is worse than no dashboard. The listings *table* reads the provider
directly, because there the raw records are the point.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st
from langchain_core.tools import BaseTool

from real_estate_agent.providers.base import ListingsProvider
from real_estate_agent.providers.mock import MockListingsProvider
from real_estate_agent.tools import make_market_tools

# The mock holds 66 listings; a real provider could hold far more. This is the
# same ceiling `market_statistics` and `qualify_lead` use internally.
_SEARCH_LIMIT = 500


@st.cache_resource
def get_provider() -> ListingsProvider:
    """The listings data source.

    Swapping this for a real feed is the same one-line change described in the
    README -- the dashboard depends on the ``ListingsProvider`` protocol, not on
    the mock.
    """
    return MockListingsProvider()


@st.cache_resource
def _market_tools() -> dict[str, BaseTool]:
    """The CMA tools, bound to the provider and looked up by name."""
    return {tool.name: tool for tool in make_market_tools(get_provider())}


@st.cache_data(ttl="15m", max_entries=64, show_spinner=False)
def market_snapshot(
    city: str,
    state: str | None,
    property_type: str | None,
    months_back: int,
) -> dict[str, Any]:
    """Supply, pricing and absorption for one market.

    Returns the parsed payload of ``market_statistics`` -- the same JSON the
    market-analyst specialist reads, including its ``interpretation_hint``.
    """
    payload = _market_tools()["market_statistics"].invoke(
        {
            "city": city,
            "state": state,
            "property_type": property_type,
            "months_back": months_back,
        }
    )
    return json.loads(str(payload))


@st.cache_data(ttl="15m", max_entries=64, show_spinner=False)
def listings_frame(
    city: str,
    state: str | None,
    property_type: str | None,
    status: str | None,
) -> pd.DataFrame:
    """Raw listing records as a frame, for the table, charts and map.

    ``status=None`` means any status, matching the provider's own contract.
    """
    listings = get_provider().search(
        city=city,
        state=state,
        property_type=property_type,
        status=status,
        limit=_SEARCH_LIMIT,
    )
    frame = pd.DataFrame([listing.as_dict() for listing in listings])
    if frame.empty:
        return frame
    # `sold_date` is ISO-8601 text on the dataclass; the table and any date
    # filtering want a real datetime.
    frame["sold_date"] = pd.to_datetime(frame["sold_date"], errors="coerce")
    return frame


@st.cache_data(ttl="15m", show_spinner=False)
def dataset_choices() -> tuple[list[str], list[str]]:
    """Cities and property types actually present, for the filter widgets.

    Read off the data rather than hardcoded, so pointing ``get_provider`` at a
    real feed repopulates the filters with no further change.
    """
    listings = get_provider().search(status=None, limit=_SEARCH_LIMIT)
    cities = sorted({listing.city for listing in listings})
    property_types = sorted({listing.property_type for listing in listings})
    return cities, property_types


def state_for_city(city: str) -> str | None:
    """The state code for a city, so the filters need only ask for one."""
    for listing in get_provider().search(city=city, status=None, limit=1):
        return listing.state
    return None

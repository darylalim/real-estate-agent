"""The app's single listings data source.

Both pages read through this one instance. The chat agent used to take
``build_agent``'s default provider while the dashboard constructed its own — on
the deterministic mock the two are indistinguishable, which is exactly why it
would have survived review, but against a real feed the analyst and the
dashboard would have been reading two different snapshots. That is the
divergence ``market_data`` exists to prevent, arriving by another route.

It also keeps the README's claim honest: taking the agent live is one change,
here, not one per page.
"""

from __future__ import annotations

import streamlit as st

from real_estate_agent.providers.base import ListingsProvider
from real_estate_agent.providers.mock import MockListingsProvider


@st.cache_resource
def get_provider() -> ListingsProvider:
    """The listings data source, built once for the server's lifetime.

    Implement ``search`` / ``get`` / ``comparables`` against a real feed and
    return it here — the tools, subagents and prompts depend on the
    ``ListingsProvider`` protocol, not on the mock.
    """
    return MockListingsProvider()

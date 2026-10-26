"""The app's single listings data source.

Both pages read through this one instance. The chat agent used to take
``build_agent``'s default provider while the dashboard constructed its own — on
the deterministic mock the two are indistinguishable, which is exactly why it
would have survived review, but against a real feed the analyst and the
dashboard would have been reading two different snapshots. That is the
divergence ``market_data`` exists to prevent, arriving by another route.

Which provider that is gets decided one level further down, by
``providers.default_provider()``, the same call ``main.py`` makes -- so taking
the agent live is one change for the CLI and both pages, not one per entry
point.
"""

from __future__ import annotations

import streamlit as st

from real_estate_agent.providers import ListingsProvider, default_provider


@st.cache_resource
def get_provider() -> ListingsProvider:
    """The listings data source, built once for the server's lifetime.

    Cached here rather than in ``default_provider`` so the package stays free of
    Streamlit; a real feed is returned from there, not here.
    """
    return default_provider()

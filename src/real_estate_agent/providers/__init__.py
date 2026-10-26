"""Listings data sources."""

from real_estate_agent.providers.base import (
    DiagnosticListingsProvider,
    Listing,
    ListingsProvider,
    MarketsListingsProvider,
)
from real_estate_agent.providers.mock import MockListingsProvider


def default_provider() -> ListingsProvider:
    """The listings data source both entry points run against.

    This is the one place to return a real provider: ``main.py`` and the
    Streamlit app (``ui/provider.py``) both call it, so a swap here reaches the
    CLI and the browser together. ``build_agent``'s own fallback to the mock is
    for tests and library callers, and is not consulted by either entry point.
    """
    return MockListingsProvider()


__all__ = [
    "DiagnosticListingsProvider",
    "Listing",
    "ListingsProvider",
    "MarketsListingsProvider",
    "MockListingsProvider",
    "default_provider",
]

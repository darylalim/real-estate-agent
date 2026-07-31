"""Property search tools.

Built as a factory closing over a ``ListingsProvider`` so the data source stays
injectable — tests and a real MLS feed use the same tool surface.
"""

from __future__ import annotations

import json

from langchain.tools import tool
from langchain_core.tools import BaseTool

from real_estate_agent.providers.base import ListingsProvider


def make_listing_tools(provider: ListingsProvider) -> list[BaseTool]:
    """Return the property-search tools bound to ``provider``."""

    @tool
    def search_listings(
        city: str | None = None,
        state: str | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        min_beds: int | None = None,
        min_baths: float | None = None,
        property_type: str | None = None,
        status: str = "active",
        limit: int = 25,
    ) -> str:
        """Search property listings and return matches as JSON.

        Use this to build or narrow a buyer shortlist. Every filter is optional;
        omit a filter to leave that dimension unconstrained.

        Args:
            city: City name, e.g. "Austin".
            state: Two-letter state code, e.g. "TX".
            min_price: Minimum list price in whole dollars.
            max_price: Maximum list price in whole dollars.
            min_beds: Minimum bedroom count.
            min_baths: Minimum bathroom count (halves allowed, e.g. 2.5).
            property_type: One of "single_family", "condo", "townhouse".
            status: One of "active", "pending", "sold". Defaults to "active".
            limit: Maximum number of listings to return.
        """
        results = provider.search(
            city=city,
            state=state,
            min_price=min_price,
            max_price=max_price,
            min_beds=min_beds,
            min_baths=min_baths,
            property_type=property_type,
            status=status,
            limit=limit,
        )
        return json.dumps(
            {
                "count": len(results),
                "filters_applied": {
                    key: value
                    for key, value in {
                        "city": city,
                        "state": state,
                        "min_price": min_price,
                        "max_price": max_price,
                        "min_beds": min_beds,
                        "min_baths": min_baths,
                        "property_type": property_type,
                        "status": status,
                    }.items()
                    if value is not None
                },
                "listings": [listing.as_dict() for listing in results],
            },
            indent=2,
        )

    @tool
    def get_listing(listing_id: str) -> str:
        """Fetch the full record for one listing by its MLS id, as JSON.

        Args:
            listing_id: The listing identifier, e.g. "MLS-1022".
        """
        listing = provider.get(listing_id)
        if listing is None:
            return json.dumps({"error": f"No listing found with id {listing_id!r}."})
        return json.dumps(listing.as_dict(), indent=2)

    return [search_listings, get_listing]

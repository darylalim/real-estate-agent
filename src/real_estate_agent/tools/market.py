"""Comparative market analysis tools.

The statistics here are computed in Python rather than left to the model — a
median is not something worth spending tokens or risking arithmetic on.
"""

from __future__ import annotations

import json
import statistics
from typing import Sequence

from langchain.tools import tool
from langchain_core.tools import BaseTool

from real_estate_agent.providers.base import Listing, ListingsProvider


def _summarize_prices(listings: Sequence[Listing]) -> dict[str, float | int | None]:
    """Central-tendency stats over a listing set, or nulls when empty."""
    if not listings:
        return {
            "count": 0,
            "median_price": None,
            "mean_price": None,
            "median_price_per_sqft": None,
            "median_sqft": None,
            "median_days_on_market": None,
        }

    prices = [
        listing.sold_price if listing.sold_price is not None else listing.price
        for listing in listings
    ]
    return {
        "count": len(listings),
        "median_price": int(statistics.median(prices)),
        "mean_price": int(statistics.fmean(prices)),
        "median_price_per_sqft": round(
            statistics.median([listing.price_per_sqft for listing in listings]), 2
        ),
        "median_sqft": int(statistics.median([listing.sqft for listing in listings])),
        "median_days_on_market": int(
            statistics.median([listing.days_on_market for listing in listings])
        ),
    }


def make_market_tools(provider: ListingsProvider) -> list[BaseTool]:
    """Return the CMA tools bound to ``provider``."""

    @tool
    def find_comparables(
        listing_id: str,
        radius_miles: float = 1.5,
        months_back: int = 6,
        limit: int = 8,
    ) -> str:
        """Find recently sold comparables for a subject property, as JSON.

        Returns the comp set plus aggregate statistics and an indicated value
        range derived from comp price-per-sqft applied to the subject's size.
        This is the primary input to a CMA.

        Args:
            listing_id: Subject property id, e.g. "MLS-1022".
            radius_miles: Search radius around the subject property.
            months_back: Only include sales closed within this many months.
            limit: Maximum number of comparables to return.
        """
        subject = provider.get(listing_id)
        if subject is None:
            return json.dumps({"error": f"No listing found with id {listing_id!r}."})

        comps = list(
            provider.comparables(
                listing_id, radius_miles=radius_miles, months_back=months_back, limit=limit
            )
        )
        stats = _summarize_prices(comps)

        indicated_value = None
        if comps and subject.sqft:
            ppsf_values = [listing.price_per_sqft for listing in comps]
            indicated_value = {
                "low": int(min(ppsf_values) * subject.sqft),
                "midpoint": int(statistics.median(ppsf_values) * subject.sqft),
                "high": int(max(ppsf_values) * subject.sqft),
                "basis": "comp price-per-sqft range applied to subject square footage",
            }

        return json.dumps(
            {
                "subject": subject.as_dict(),
                "search": {
                    "radius_miles": radius_miles,
                    "months_back": months_back,
                    "comps_found": len(comps),
                },
                "comp_statistics": stats,
                "indicated_value_range": indicated_value,
                "comparables": [listing.as_dict() for listing in comps],
            },
            indent=2,
        )

    @tool
    def market_statistics(
        city: str,
        state: str | None = None,
        property_type: str | None = None,
        months_back: int = 12,
    ) -> str:
        """Summarize supply, pricing, and absorption for a market, as JSON.

        Compares active inventory against closed sales so you can speak to
        pricing pressure and days-on-market, not just a single listing.

        Args:
            city: City name, e.g. "Austin".
            state: Two-letter state code, e.g. "TX".
            property_type: Optionally restrict to one of "single_family",
                "condo", "townhouse".
            months_back: Window for closed-sale statistics.
        """
        active = list(
            provider.search(
                city=city, state=state, property_type=property_type, status="active", limit=500
            )
        )
        sold_all = list(
            provider.search(
                city=city, state=state, property_type=property_type, status="sold", limit=500
            )
        )

        active_stats = _summarize_prices(active)
        sold_stats = _summarize_prices(sold_all)

        # Months of inventory = active supply / average monthly absorption.
        months_of_inventory = None
        if sold_all and months_back:
            monthly_absorption = len(sold_all) / months_back
            if monthly_absorption > 0:
                months_of_inventory = round(len(active) / monthly_absorption, 1)

        return json.dumps(
            {
                "market": {
                    "city": city,
                    "state": state,
                    "property_type": property_type or "all",
                },
                "active_inventory": active_stats,
                "closed_sales": sold_stats,
                "months_of_inventory": months_of_inventory,
                "interpretation_hint": (
                    "Under ~4 months of inventory generally indicates a seller's market; "
                    "over ~6 months indicates a buyer's market."
                ),
            },
            indent=2,
        )

    return [find_comparables, market_statistics]

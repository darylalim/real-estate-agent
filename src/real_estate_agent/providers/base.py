"""The seam between the agent and whatever listings data source you plug in.

``ListingsProvider`` is the only thing the tools depend on. Swapping the mock
for a real MLS / Bridge / Zillow feed means writing one class that satisfies
this protocol — no tool, subagent, or prompt changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class Listing:
    """One property record, normalized across providers."""

    listing_id: str
    address: str
    city: str
    state: str
    zip_code: str
    price: int
    beds: int
    baths: float
    sqft: int
    lot_sqft: int
    year_built: int
    property_type: str
    status: str  # "active" | "pending" | "sold"
    days_on_market: int
    latitude: float
    longitude: float
    sold_price: int | None = None
    sold_date: str | None = None  # ISO-8601 date
    hoa_monthly: int | None = None
    description: str = ""

    @property
    def price_per_sqft(self) -> float:
        basis = self.sold_price if self.sold_price is not None else self.price
        return round(basis / self.sqft, 2) if self.sqft else 0.0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["price_per_sqft"] = self.price_per_sqft
        return payload


class ListingsProvider(Protocol):
    """Implement this against a real feed to take the agent off mock data."""

    def search(
        self,
        *,
        city: str | None = None,
        state: str | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        min_beds: int | None = None,
        min_baths: float | None = None,
        property_type: str | None = None,
        status: str | None = "active",
        limit: int = 25,
    ) -> Sequence[Listing]:
        """Return listings matching the filters, most relevant first.

        ``status`` defaults to active-only; pass ``None`` for any status.
        """
        ...

    def get(self, listing_id: str) -> Listing | None:
        """Return a single listing, or None if the id is unknown."""
        ...

    def comparables(
        self,
        listing_id: str,
        *,
        radius_miles: float = 1.5,
        months_back: int = 6,
        limit: int = 8,
        max_sqft_delta_pct: float | None = 0.30,
    ) -> Sequence[Listing]:
        """Return recently sold properties suitable as comps for ``listing_id``.

        ``max_sqft_delta_pct`` screens out candidates whose living area differs
        from the subject by more than this fraction. The CMA methodology
        discards such comps anyway, so returning them only wastes the analyst's
        tokens. Pass ``None`` to disable the screen.
        """
        ...

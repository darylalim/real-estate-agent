"""Deterministic in-memory listings, so the agent runs end-to-end with no feed.

The dataset is generated from a fixed seed: same listings, same prices, same
comps on every run. That makes agent behaviour reproducible while you tune
prompts — swap in a real provider once the behaviour is right.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Sequence

from real_estate_agent.providers.base import Listing

_SEED = 1337

# The dataset's "now". Single source of truth: generation and every recency
# filter read this, so they cannot drift apart and silently disable the
# close-date screen.
_TODAY = date(2026, 7, 31)

_MARKETS = [
    # (city, state, zip, center lat/lon, base $/sqft)
    ("Austin", "TX", "78704", 30.2500, -97.7594, 545),
    ("Austin", "TX", "78745", 30.2100, -97.7900, 415),
    ("Round Rock", "TX", "78664", 30.5083, -97.6789, 305),
]

_STREETS = [
    "Bluebonnet", "Live Oak", "Barton Springs", "Cypress", "Mesquite",
    "Pecan Grove", "Shoal Creek", "Travis Heights", "Wildflower", "Riverbend",
]

_PROPERTY_TYPES = ["single_family", "condo", "townhouse"]


def _build_dataset() -> list[Listing]:
    rng = random.Random(_SEED)
    today = _TODAY
    listings: list[Listing] = []
    counter = 1000

    for city, state, zip_code, lat, lon, base_ppsf in _MARKETS:
        for _ in range(22):
            counter += 1
            property_type = rng.choice(_PROPERTY_TYPES)
            beds = rng.choice([2, 3, 3, 4, 4, 5])
            baths = rng.choice([1.5, 2.0, 2.0, 2.5, 3.0, 3.5])
            # Tight sigma on purpose: real markets cluster by size, and a wide
            # spread over a small dataset leaves every subject without a
            # size-matched comp, so no CMA is ever possible.
            sqft = int(rng.gauss(380 * beds + 520, 130))
            sqft = max(720, min(sqft, 4200))
            year_built = rng.choice([1958, 1972, 1985, 1998, 2006, 2015, 2021])

            # Price anchors on $/sqft, then flexes for age and property type.
            ppsf = base_ppsf * rng.uniform(0.88, 1.14)
            if year_built >= 2015:
                ppsf *= 1.08
            elif year_built <= 1975:
                ppsf *= 0.93
            if property_type == "condo":
                ppsf *= 0.9
            price = int(round(sqft * ppsf, -3))

            # Just under half of inventory is recent sold comps, so CMAs have
            # something to work with at the default 6-month window.
            roll = rng.random()
            if roll < 0.45:
                status = "sold"
                days_back = rng.randint(5, 330)
                sold_date = today - timedelta(days=days_back)
                sold_price = int(round(price * rng.uniform(0.94, 1.03), -3))
                days_on_market = rng.randint(4, 95)
            elif roll < 0.5:
                status = "pending"
                sold_date, sold_price = None, None
                days_on_market = rng.randint(3, 40)
            else:
                status = "active"
                sold_date, sold_price = None, None
                days_on_market = rng.randint(1, 120)

            listings.append(
                Listing(
                    listing_id=f"MLS-{counter}",
                    address=f"{rng.randint(100, 9900)} {rng.choice(_STREETS)} "
                    f"{rng.choice(['St', 'Ave', 'Dr', 'Ln'])}",
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    price=price,
                    beds=beds,
                    baths=baths,
                    sqft=sqft,
                    # Clamped like sqft above: an unclamped Gaussian goes
                    # negative below -2.7 sigma, and a negative lot size flows
                    # straight into the CMA's lot-size adjustment row.
                    lot_sqft=max(1200, int(rng.gauss(7000, 2600)))
                    if property_type != "condo"
                    else 0,
                    year_built=year_built,
                    property_type=property_type,
                    status=status,
                    days_on_market=days_on_market,
                    # ~±0.5 mi of the ZIP centroid, so a realistic 1-1.5 mile
                    # comp radius actually returns intra-ZIP neighbours.
                    latitude=round(lat + rng.uniform(-0.007, 0.007), 6),
                    longitude=round(lon + rng.uniform(-0.008, 0.008), 6),
                    sold_price=sold_price,
                    sold_date=sold_date.isoformat() if sold_date else None,
                    hoa_monthly=rng.choice([0, 0, 45, 120, 340])
                    if property_type != "single_family"
                    else 0,
                    description=(
                        f"{beds} bed / {baths} bath {property_type.replace('_', ' ')} "
                        f"in {city}, built {year_built}."
                    ),
                )
            )

    return listings


def _miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in miles — good enough for comp radius filtering."""
    radius_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_miles * 2 * math.asin(math.sqrt(a))


class MockListingsProvider:
    """Satisfies ``ListingsProvider`` against a fixed synthetic dataset."""

    def __init__(self) -> None:
        self._listings = _build_dataset()
        self._by_id = {listing.listing_id: listing for listing in self._listings}
        self._today = _TODAY

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
        sold_within_months: int | None = None,
        limit: int = 25,
    ) -> Sequence[Listing]:
        sold_cutoff = (
            self._today - timedelta(days=int(sold_within_months * 30.44))
            if sold_within_months is not None
            else None
        )

        def closed_recently(listing: Listing) -> bool:
            if sold_cutoff is None:
                return True
            if listing.sold_date is None:
                return False
            return date.fromisoformat(listing.sold_date) >= sold_cutoff

        results = [
            listing
            for listing in self._listings
            # Case-insensitive on every string filter. A capitalised "Active"
            # returning zero listings reads to the agent as an empty market.
            if (status is None or listing.status.lower() == status.lower())
            and (city is None or listing.city.lower() == city.lower())
            and (state is None or listing.state.lower() == state.lower())
            and (min_price is None or listing.price >= min_price)
            and (max_price is None or listing.price <= max_price)
            and (min_beds is None or listing.beds >= min_beds)
            and (min_baths is None or listing.baths >= min_baths)
            and (
                property_type is None
                or listing.property_type.lower() == property_type.lower()
            )
            and closed_recently(listing)
        ]
        # Freshest inventory first — mirrors how most MLS feeds default.
        results.sort(key=lambda listing: (listing.days_on_market, listing.price))
        return results[:limit]

    def get(self, listing_id: str) -> Listing | None:
        return self._by_id.get(listing_id)

    def comparables(
        self,
        listing_id: str,
        *,
        radius_miles: float = 1.5,
        months_back: int = 6,
        limit: int = 8,
        max_sqft_delta_pct: float | None = 0.30,
    ) -> Sequence[Listing]:
        _subject, comps, _rejected = self.comparables_with_diagnostics(
            listing_id,
            radius_miles=radius_miles,
            months_back=months_back,
            limit=limit,
            max_sqft_delta_pct=max_sqft_delta_pct,
        )
        return comps

    def comparables_with_diagnostics(
        self,
        listing_id: str,
        *,
        radius_miles: float = 1.5,
        months_back: int = 6,
        limit: int = 8,
        max_sqft_delta_pct: float | None = 0.30,
    ) -> tuple[Listing | None, list[Listing], dict[str, int]]:
        """Comps plus a count of what was screened out, and why.

        Silent screening is worse than no screening: an analyst that cannot see
        it rejected nine candidates on size has no way to tell a thin comp set
        from a thin market.
        """
        subject = self._by_id.get(listing_id)
        if subject is None:
            return None, [], {}

        cutoff = self._today - timedelta(days=int(months_back * 30.44))
        # `not_sold` is a near-constant floor (every active and pending
        # listing), so it is kept separate from `stale`. Lumped together it
        # swamps the recency signal the analyst actually needs.
        rejected = {"not_sold": 0, "stale": 0, "outside_radius": 0, "size_mismatch": 0}
        scored: list[tuple[float, Listing]] = []

        for candidate in self._listings:
            if candidate.listing_id == subject.listing_id:
                continue
            if candidate.status != "sold" or candidate.sold_date is None:
                rejected["not_sold"] += 1
                continue
            if date.fromisoformat(candidate.sold_date) < cutoff:
                rejected["stale"] += 1
                continue

            distance = _miles_between(
                subject.latitude, subject.longitude, candidate.latitude, candidate.longitude
            )
            if distance > radius_miles:
                rejected["outside_radius"] += 1
                continue

            sqft_delta = abs(candidate.sqft - subject.sqft) / max(subject.sqft, 1)

            # Hard size screen. Without it the ranking happily returns a comp
            # 175% larger than the subject — which the CMA methodology then
            # discards anyway, after the analyst has spent tokens adjusting it.
            if max_sqft_delta_pct is not None and sqft_delta > max_sqft_delta_pct:
                rejected["size_mismatch"] += 1
                continue

            bed_delta = abs(candidate.beds - subject.beds)
            age_delta = abs(candidate.year_built - subject.year_built) / 100
            score = distance + (sqft_delta * 2) + (bed_delta * 0.35) + age_delta
            scored.append((score, candidate))

        scored.sort(key=lambda pair: pair[0])
        return subject, [listing for _, listing in scored[:limit]], rejected

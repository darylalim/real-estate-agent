"""Deterministic in-memory listings, so the agent runs end-to-end with no feed.

The dataset is generated from a fixed seed: same listings, same prices, same
comps on every run. That makes agent behaviour reproducible while you tune
prompts — swap in a real provider once the behaviour is right.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import date, timedelta

from real_estate_agent.providers.base import Listing

_SEED = 1337

# The dataset's "now". Single source of truth: generation and every recency
# filter read this, so they cannot drift apart and silently disable the
# close-date screen.
_TODAY = date(2026, 7, 31)

_MARKETS = [
    # (city, state, zip, center lat/lon, base $/sqft, lat jitter, lon jitter)
    #
    # Two islands, priced far apart on purpose, so budget feasibility has
    # something real to bite on. The Honolulu ZIPs are ~2.1 miles apart, so the
    # 1.5-mile default comp radius mostly separates them -- "mostly" because the
    # jitter below lets a small tail of cross-ZIP pairs fall inside it. Hilo is
    # on Hawaii island, 200+ miles away, so it cannot contaminate an Oahu comp
    # set at any radius a CMA would use; the inter-island gap does that job for
    # free, with no filter required.
    #
    # The jitter is per-market because one box does not fit these three. Waikiki
    # is a ~0.35-mile strip pinned between the Ala Wai Canal and the shoreline,
    # so the +/-0.007 box the mainland dataset used put six of its twenty-two
    # listings in the Pacific -- which `st.map` on the market page renders
    # faithfully, on the one page with no model in the loop to explain it.
    ("Honolulu", "HI", "96815", 21.2795, -157.8292, 850, 0.0045, 0.0065),  # Waikiki
    ("Honolulu", "HI", "96816", 21.2836, -157.7967, 780, 0.0045, 0.0055),  # Kaimuki
    ("Hilo", "HI", "96720", 19.7220, -155.0870, 320, 0.0070, 0.0080),
]

# Keyed by ZIP, not shared: a single pool put Kalakaua (the Waikiki street) in
# Hilo and Banyan (Hilo) in Waikiki, which reads as obviously synthetic to
# anyone who knows the islands. Each list is exactly ten entries on purpose --
# `random.choice` rejection-samples through `_randbelow(len(seq))`, so a list of
# a different length consumes a different number of raw draws and shifts every
# subsequent value in the dataset. Change the names freely; keep the count.
_STREETS = {
    "96815": [  # Waikiki
        "Kalakaua Ave", "Kuhio Ave", "Ala Wai Blvd", "Seaside Ave", "Lewers St",
        "Kaiulani Ave", "Beach Walk", "Nohonani St", "Olohana St", "Paoakalani Ave",
    ],
    "96816": [  # Kaimuki / Diamond Head
        "Waialae Ave", "Koko Head Ave", "Wilhelmina Rise", "Pahoa Ave", "Kilauea Ave",
        "Harding Ave", "Sierra Dr", "Palolo Ave", "Maunaloa Ave", "Diamond Head Rd",
    ],
    "96720": [  # Hilo
        "Kinoole St", "Waianuenue Ave", "Kapiolani St", "Ponahawai St", "Haili St",
        "Komohana St", "Puainako St", "Kamehameha Ave", "Banyan Dr", "Kilauea Ave",
    ],
}

_PROPERTY_TYPES = ["single_family", "condo", "townhouse"]


def _build_dataset() -> list[Listing]:
    rng = random.Random(_SEED)
    today = _TODAY
    listings: list[Listing] = []
    counter = 1000

    for city, state, zip_code, lat, lon, base_ppsf, d_lat, d_lon in _MARKETS:
        for _ in range(22):
            counter += 1
            property_type = rng.choice(_PROPERTY_TYPES)
            beds = rng.choice([2, 3, 3, 4, 4, 5])
            baths = rng.choice([1.5, 2.0, 2.0, 2.5, 3.0, 3.5])
            # Tight sigma on purpose: real markets cluster by size, and a wide
            # spread over a small dataset leaves every subject without a
            # size-matched comp, so no CMA is ever possible. Hawaii homes run
            # materially smaller than mainland ones, so both the intercept and
            # the slope are modest -- but what matters to the +/-30% size screen
            # is the *ratio*, not the absolute sigma, so sigma is set to hold the
            # coefficient of variation near 8%. Retune the two together or comp
            # availability moves without anything looking wrong.
            sqft = int(rng.gauss(300 * beds + 350, 100))
            sqft = max(600, min(sqft, 3200))
            # Waikiki's condo towers and Hilo's plantation-era stock both sit at
            # the old end; the two post-2015 values keep the new-build premium
            # below reachable, and the two pre-1975 values the age discount.
            year_built = rng.choice([1941, 1963, 1978, 1992, 2004, 2016, 2023])

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
            #
            # The pending band is 0.45-0.58 rather than the 0.45-0.50 it started
            # as, because a 5% band over 66 draws has a ~3.4% chance of never
            # firing and at seed 1337 that is exactly what happened: zero
            # pending listings, while `search_listings` went on advertising
            # `status="pending"` as a filter that returns an empty market.
            # Nothing failed — the branch was simply unreachable data, and
            # `comparables_with_diagnostics` still described `not_sold` as
            # covering "every active and pending listing". Widened so the status
            # the tool offers actually exists, and pinned by
            # `test_every_advertised_filter_value_exists_in_the_dataset` so a future
            # seed cannot quietly empty it again.
            roll = rng.random()
            if roll < 0.45:
                status = "sold"
                days_back = rng.randint(5, 330)
                sold_date = today - timedelta(days=days_back)
                sold_price = int(round(price * rng.uniform(0.94, 1.03), -3))
                days_on_market = rng.randint(4, 95)
            elif roll < 0.58:
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
                    # The suffix is part of the name, not a separate draw. Drawn
                    # independently it produced "Kalakaua St" and "Ala Wai Ln"
                    # for streets that are an Ave and a Blvd, and could never
                    # produce Beach Walk or Wilhelmina Rise at all -- invisible
                    # with invented mainland names, wrong the moment the names
                    # became real ones. Range capped at 3900 for the same
                    # reason: none of these streets is numbered into the 9000s.
                    address=f"{rng.randint(100, 3900)} {rng.choice(_STREETS[zip_code])}",
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
                    lot_sqft=max(1500, int(rng.gauss(6000, 2200)))
                    if property_type != "condo"
                    else 0,
                    year_built=year_built,
                    property_type=property_type,
                    status=status,
                    days_on_market=days_on_market,
                    # Per-market box (see _MARKETS), sized so a realistic
                    # 1-1.5 mile comp radius returns intra-ZIP neighbours
                    # without scattering listings outside the ZIP's real extent.
                    latitude=round(lat + rng.uniform(-d_lat, d_lat), 6),
                    longitude=round(lon + rng.uniform(-d_lon, d_lon), 6),
                    sold_price=sold_price,
                    sold_date=sold_date.isoformat() if sold_date else None,
                    # No zero here, unlike the mainland dataset: a Hawaii condo
                    # or townhouse without an association fee does not exist,
                    # and the maintenance fee is large enough relative to price
                    # that a buyer's affordability answer is wrong without it.
                    hoa_monthly=rng.choice([165, 285, 480, 720, 1150])
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

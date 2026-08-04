"""Deterministic in-memory listings, so the agent runs end-to-end with no feed.

The dataset is generated from a fixed seed: same listings, same prices, same
comps on every run. That makes agent behaviour reproducible while you tune
prompts — swap in a real provider once the behaviour is right.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from real_estate_agent.providers.base import Listing

_SEED = 1337

# The dataset's "now". Single source of truth: generation and every recency
# filter read this, so they cannot drift apart and silently disable the
# close-date screen.
_TODAY = date(2026, 7, 31)

# Listings generated per market. Deliberately sold-heavy overall (see the status
# bands in _build_dataset): months-of-inventory compares standing inventory
# against a *year* of sales, so a market at 4 months needs roughly three times as
# many closed records as active ones. Holding that ratio while keeping active
# inventory large enough to search is what sets this number -- at 22 per market
# the same ratio left Hilo with four active listings.
_PER_MARKET = 36


def _pool(**counts: int) -> tuple[str, ...]:
    """A weighted draw pool: ``_pool(condo=18, townhouse=2)`` is 90% condo.

    Drawn from uniformly, so the weights are just repetition. Each market's pool
    is twenty entries, which makes a count read directly as a percentage.
    """
    return tuple(name for name, count in counts.items() for _ in range(count))


@dataclass(frozen=True)
class _Market:
    """One submarket. Everything that differs between ZIPs lives here.

    A tuple was fine for four fields and unreadable at nine. The fields that
    earned their place are the ones where a single global value was wrong for at
    least one of these three ZIPs -- jitter, property mix, and association fee.
    """

    city: str
    state: str
    zip_code: str
    latitude: float
    longitude: float
    base_ppsf: int
    # Half-width of the coordinate box, in degrees. Per-market because one box
    # does not fit these three: Waikiki is a ~0.35-mile strip pinned between the
    # Ala Wai Canal and the shoreline, so the +/-0.007 box inherited from a
    # sprawling mainland ZIP put six of its listings in the Pacific -- which
    # `st.map` on the market page renders faithfully, on the one page with no
    # model in the loop to explain it.
    jitter_lat: float
    jitter_lon: float
    # Monthly association fee per square foot. Oahu's high-rise stock carries
    # roughly $0.65/sqft/mo; Hilo's is about half that. Derived rather than drawn
    # from a flat menu, because an uncorrelated fee put $165/mo on a $1.3M
    # Waikiki condo and $1,150/mo on a $380k Hilo one -- and at this magnitude
    # the fee drives the affordability answer rather than decorating it.
    hoa_psf: float
    # Scoped to the ZIP: a shared pool put Kalakaua (the Waikiki street) in Hilo
    # and Banyan (Hilo) in Waikiki. Suffixes are part of the name, so Beach Walk
    # and Wilhelmina Rise can exist and Kalakaua cannot come out a "St".
    streets: tuple[str, ...]
    # Waikiki is a wall of condo towers with essentially no detached housing;
    # Hilo is overwhelmingly single-family. A uniform draw across all three ZIPs
    # put 2,100-sqft single-family homes on Seaside Ave.
    types: tuple[str, ...]


# Two islands, priced far apart on purpose, so budget feasibility has something
# real to bite on. The Honolulu ZIPs are ~2.1 miles apart, so the 1.5-mile
# default comp radius mostly separates them -- "mostly" because the jitter lets a
# small tail of cross-ZIP pairs fall inside it. Hilo is on Hawaii island, 200+
# miles away, so it cannot contaminate an Oahu comp set at any radius a CMA would
# use; the inter-island gap does that job for free, with no filter required.
_MARKETS = [
    _Market(
        city="Honolulu", state="HI", zip_code="96815",  # Waikiki
        latitude=21.2795, longitude=-157.8292, base_ppsf=850,
        jitter_lat=0.0045, jitter_lon=0.0065, hoa_psf=0.65,
        streets=(
            "Kalakaua Ave", "Kuhio Ave", "Ala Wai Blvd", "Seaside Ave", "Lewers St",
            "Kaiulani Ave", "Beach Walk", "Nohonani St", "Olohana St", "Paoakalani Ave",
        ),
        types=_pool(condo=18, townhouse=2),
    ),
    _Market(
        city="Honolulu", state="HI", zip_code="96816",  # Kaimuki / Diamond Head
        latitude=21.2836, longitude=-157.7967, base_ppsf=780,
        jitter_lat=0.0045, jitter_lon=0.0055, hoa_psf=0.65,
        streets=(
            "Waialae Ave", "Koko Head Ave", "Wilhelmina Rise", "Pahoa Ave", "Kilauea Ave",
            "Harding Ave", "Sierra Dr", "Palolo Ave", "Maunaloa Ave", "Diamond Head Rd",
        ),
        types=_pool(single_family=12, condo=5, townhouse=3),
    ),
    _Market(
        city="Hilo", state="HI", zip_code="96720",
        latitude=19.7220, longitude=-155.0870, base_ppsf=320,
        jitter_lat=0.0070, jitter_lon=0.0080, hoa_psf=0.35,
        streets=(
            "Kinoole St", "Waianuenue Ave", "Kapiolani St", "Ponahawai St", "Haili St",
            "Komohana St", "Puainako St", "Kamehameha Ave", "Banyan Dr", "Kilauea Ave",
        ),
        types=_pool(single_family=17, condo=2, townhouse=1),
    ),
]


def _build_dataset() -> list[Listing]:
    rng = random.Random(_SEED)
    today = _TODAY
    listings: list[Listing] = []
    counter = 1000

    for market in _MARKETS:
        for _ in range(_PER_MARKET):
            counter += 1
            property_type = rng.choice(market.types)
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
            # Beds, baths and size are drawn independently, which produced a
            # 660-sqft two-bed with three full baths. Clamped against the size
            # already drawn rather than redrawn, so this costs no rng call and
            # cannot shift the stream. `baths` is not an input to the comp score,
            # so comp sets do not move either.
            baths = min(baths, 1.0 + 0.5 * (sqft // 350))
            # Waikiki's condo towers and Hilo's plantation-era stock both sit at
            # the old end; the two post-2015 values keep the new-build premium
            # below reachable, and the two pre-1975 values the age discount.
            year_built = rng.choice([1941, 1963, 1978, 1992, 2004, 2016, 2023])

            # Price anchors on $/sqft, then flexes for age and property type.
            ppsf = market.base_ppsf * rng.uniform(0.88, 1.14)
            if year_built >= 2015:
                ppsf *= 1.08
            elif year_built <= 1975:
                ppsf *= 0.93
            if property_type == "condo":
                ppsf *= 0.9
            price = int(round(sqft * ppsf, -3))

            # Deliberately sold-heavy: ~68% sold, ~10% pending, ~22% active.
            #
            # This is what makes months-of-inventory read correctly. MOI divides
            # standing inventory by the monthly rate of a *year* of sales, so at
            # a 12-month window MOI = 12 x active / sold -- and a market at four
            # months therefore needs three times as many closed records as active
            # ones. The old 45/13/42 split could not express that at any dataset
            # size: it reported 10-11 months for Honolulu, which the tool's own
            # `interpretation_hint` labels a deep buyer's market, for a city that
            # has run 3-5 months for years. Nothing was wrong with the
            # arithmetic; the fixture simply had no year of sales behind it.
            #
            # The pending band exists at all because a narrow one silently
            # emptied: at seed 1337 the original 5-point band never fired, and
            # `search_listings` went on advertising `status="pending"` as a
            # filter over a market with no homes under contract. Nothing failed
            # -- the branch was unreachable data. Ten points now, and pinned by
            # `test_every_advertised_filter_value_exists_in_the_dataset` so a
            # future seed cannot quietly empty it again.
            roll = rng.random()
            if roll < 0.68:
                status = "sold"
                days_back = rng.randint(5, 330)
                sold_date = today - timedelta(days=days_back)
                sold_price = int(round(price * rng.uniform(0.94, 1.03), -3))
                days_on_market = rng.randint(4, 95)
            elif roll < 0.78:
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
                    address=f"{rng.randint(100, 3900)} {rng.choice(market.streets)}",
                    city=market.city,
                    state=market.state,
                    zip_code=market.zip_code,
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
                    latitude=round(
                        market.latitude
                        + rng.uniform(-market.jitter_lat, market.jitter_lat),
                        6,
                    ),
                    longitude=round(
                        market.longitude
                        + rng.uniform(-market.jitter_lon, market.jitter_lon),
                        6,
                    ),
                    sold_price=sold_price,
                    sold_date=sold_date.isoformat() if sold_date else None,
                    # Derived from size and market, not drawn from a flat menu.
                    # A Hawaii condo or townhouse without an association fee does
                    # not exist, and at these magnitudes the fee drives the
                    # affordability answer rather than decorating it -- so an
                    # uncorrelated draw is not merely imprecise, it is backwards:
                    # it put $165/mo on a $1.3M Waikiki condo and $1,150/mo on a
                    # $380k Hilo one. One `rng` call either way.
                    hoa_monthly=int(
                        round(sqft * market.hoa_psf * rng.uniform(0.85, 1.2), -1)
                    )
                    if property_type != "single_family"
                    else 0,
                    description=(
                        f"{beds} bed / {baths} bath {property_type.replace('_', ' ')} "
                        f"in {market.city}, built {year_built}."
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

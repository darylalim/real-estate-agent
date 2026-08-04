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


# The property types `search_listings` advertises to the model. `_pool` is
# validated against this, because a pool is built from keyword names and
# `_pool(single_famliy=17, ...)` would otherwise type-check, lint, and mint
# seventeen listings the tool's own Literal can never filter for --
# `test_every_advertised_filter_value_exists_in_the_dataset` only checks the
# forward direction, so nothing would have gone red.
PROPERTY_TYPES = ("single_family", "condo", "townhouse")


def _pool(**counts: int) -> tuple[str, ...]:
    """A weighted draw pool: ``_pool(condo=18, townhouse=2)`` is 90% condo.

    Drawn from uniformly, so the weights are just repetition. Each market's pool
    is twenty entries, which makes a count read directly as a percentage.

    Raises:
        ValueError: if a key is not an advertised ``PROPERTY_TYPES`` value.
    """
    unknown = sorted(set(counts) - set(PROPERTY_TYPES))
    if unknown:
        raise ValueError(
            f"not advertised property types: {unknown}. "
            f"search_listings only offers {list(PROPERTY_TYPES)}, so listings "
            "generated under any other name are unreachable by the model."
        )
    return tuple(name for name, count in counts.items() for _ in range(count))


@dataclass(frozen=True)
class _Street:
    """A real street, with the coordinates and house numbers it actually has.

    Both were global before, and both were wrong once the names became real
    ones. The number range gave "3380 Beach Walk" on a two-block street, and a
    ZIP-wide coordinate box put eleven Waikiki listings in the Pacific -- Waikiki
    is a narrow strip along a *diagonal* shoreline, so no axis-aligned rectangle
    covers it without overhanging the water. Anchoring on the street solves both,
    and is the only version where the address and the map pin agree.
    """

    name: str
    latitude: float
    longitude: float
    number_low: int
    number_high: int


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
    # Monthly association fee per square foot. Oahu's high-rise stock carries
    # roughly $0.65/sqft/mo; Hilo's is about half that. Derived rather than drawn
    # from a flat menu, because an uncorrelated fee put $165/mo on a $1.3M
    # Waikiki condo and $1,150/mo on a $380k Hilo one -- and at this magnitude
    # the fee drives the affordability answer rather than decorating it.
    hoa_psf: float
    # Scoped to the ZIP: a shared pool put Kalakaua (the Waikiki street) in Hilo
    # and Banyan (Hilo) in Waikiki. Suffixes are part of the name, so Beach Walk
    # and Wilhelmina Rise can exist and Kalakaua cannot come out a "St". Each
    # street carries its own anchor and number range -- see _Street.
    streets: tuple[_Street, ...]
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
        latitude=21.2795, longitude=-157.8292, base_ppsf=850, hoa_psf=0.65,
        # Waikiki runs NW-SE as a narrow strip between the Ala Wai Canal and the
        # beach. Kalakaua is the beach-side spine, Kuhio one block inland, Ala
        # Wai on the canal; the rest are the short cross streets between them.
        streets=(
            _Street("Kalakaua Ave", 21.2778, -157.8272, 1800, 2900),
            _Street("Kuhio Ave", 21.2792, -157.8268, 2100, 2600),
            _Street("Ala Wai Blvd", 21.2812, -157.8288, 1700, 2400),
            _Street("Seaside Ave", 21.2800, -157.8283, 300, 500),
            _Street("Lewers St", 21.2807, -157.8324, 200, 400),
            _Street("Kaiulani Ave", 21.2787, -157.8246, 100, 300),
            _Street("Beach Walk", 21.2814, -157.8340, 200, 280),
            _Street("Nohonani St", 21.2806, -157.8274, 200, 250),
            _Street("Olohana St", 21.2818, -157.8306, 400, 500),
            _Street("Paoakalani Ave", 21.2776, -157.8228, 200, 300),
        ),
        types=_pool(condo=18, townhouse=2),
    ),
    _Market(
        city="Honolulu", state="HI", zip_code="96816",  # Kaimuki / Diamond Head
        latitude=21.2836, longitude=-157.7967, base_ppsf=780, hoa_psf=0.65,
        streets=(
            _Street("Waialae Ave", 21.2835, -157.7975, 3000, 3800),
            _Street("Koko Head Ave", 21.2830, -157.7960, 700, 1200),
            _Street("Wilhelmina Rise", 21.2878, -157.7880, 1500, 2400),
            _Street("Pahoa Ave", 21.2858, -157.7912, 1000, 1800),
            _Street("Kilauea Ave", 21.2800, -157.7935, 2800, 3800),
            _Street("Harding Ave", 21.2820, -157.7965, 1100, 1900),
            _Street("Sierra Dr", 21.2884, -157.7902, 700, 1500),
            _Street("Palolo Ave", 21.2896, -157.7888, 1900, 2500),
            _Street("Maunaloa Ave", 21.2846, -157.7932, 700, 1300),
            _Street("Ocean View Dr", 21.2890, -157.7858, 1900, 2600),
        ),
        types=_pool(single_family=12, condo=5, townhouse=3),
    ),
    _Market(
        city="Hilo", state="HI", zip_code="96720",
        latitude=19.7220, longitude=-155.0870, base_ppsf=320, hoa_psf=0.35,
        # Downtown Hilo and the Waiakea side. Banyan Dr sits about a mile east on
        # the bay and is left there deliberately: it is the one address in this
        # market that a 1.5-mile comp radius will not reach from the others, so
        # the thin-comp path stays exercised by geography rather than by size.
        streets=(
            _Street("Kinoole St", 19.7195, -155.0865, 200, 2000),
            _Street("Waianuenue Ave", 19.7245, -155.0910, 100, 1200),
            _Street("Kapiolani St", 19.7215, -155.0850, 100, 900),
            _Street("Ponahawai St", 19.7230, -155.0885, 100, 1200),
            _Street("Haili St", 19.7240, -155.0875, 100, 1000),
            _Street("Komohana St", 19.7175, -155.0950, 500, 1900),
            _Street("Puainako St", 19.7130, -155.0890, 100, 2000),
            _Street("Kamehameha Ave", 19.7275, -155.0855, 100, 400),
            _Street("Banyan Dr", 19.7290, -155.0700, 71, 131),
            _Street("Kilauea Ave", 19.7205, -155.0870, 100, 1200),
        ),
        types=_pool(single_family=17, condo=2, townhouse=1),
    ),
]

# Half-width of the per-street coordinate box, in degrees -- about 0.08 x 0.12
# miles, so a listing lands on the block it is addressed to rather than anywhere
# in the ZIP. Global because it describes a city block, not a market.
_STREET_JITTER_LAT = 0.0012
_STREET_JITTER_LON = 0.0018


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

            # Deliberately sold-heavy. The bands are 75/8/17; the *realized*
            # split over 108 draws is 76 sold / 10 pending / 22 active, which is
            # what the README quotes -- band widths and realized shares are not
            # the same number and only one of them is checkable.
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
            if roll < 0.75:
                status = "sold"
                days_back = rng.randint(5, 330)
                sold_date = today - timedelta(days=days_back)
                sold_price = int(round(price * rng.uniform(0.94, 1.03), -3))
                days_on_market = rng.randint(4, 95)
            elif roll < 0.83:
                status = "pending"
                sold_date, sold_price = None, None
                days_on_market = rng.randint(3, 40)
            else:
                status = "active"
                sold_date, sold_price = None, None
                days_on_market = rng.randint(1, 120)

            # The street is drawn first because everything else about the address
            # hangs off it: the house number comes from that street's real range,
            # and the coordinate from its anchor. Drawn independently they gave
            # "3380 Beach Walk" on a two-block street, and a map pin a quarter
            # mile from the street named beside it. The suffix is part of the
            # name for the same reason -- drawn separately it produced "Kalakaua
            # St" for an Ave, and could never produce Beach Walk at all.
            street = rng.choice(market.streets)
            house_number = rng.randint(street.number_low, street.number_high)

            listings.append(
                Listing(
                    listing_id=f"MLS-{counter}",
                    address=f"{house_number} {street.name}",
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
                    # Scattered around the street's own anchor, not the ZIP's.
                    # A ZIP-wide box cannot work here: Waikiki is a narrow strip
                    # along a *diagonal* shoreline, so every axis-aligned
                    # rectangle covering it also covers open water, and eleven of
                    # its listings plotted in the Pacific on `st.map`. Shrinking
                    # the box only reduced the count -- the shape was the defect.
                    latitude=round(
                        street.latitude
                        + rng.uniform(-_STREET_JITTER_LAT, _STREET_JITTER_LAT),
                        6,
                    ),
                    longitude=round(
                        street.longitude
                        + rng.uniform(-_STREET_JITTER_LON, _STREET_JITTER_LON),
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
        same_property_type: bool = True,
    ) -> Sequence[Listing]:
        _subject, comps, _rejected = self.comparables_with_diagnostics(
            listing_id,
            radius_miles=radius_miles,
            months_back=months_back,
            limit=limit,
            max_sqft_delta_pct=max_sqft_delta_pct,
            same_property_type=same_property_type,
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
        same_property_type: bool = True,
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
        rejected = {
            "not_sold": 0,
            "stale": 0,
            "type_mismatch": 0,
            "outside_radius": 0,
            "size_mismatch": 0,
        }
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
            # Categorical, so it sits with the other categorical screens rather
            # than in the geometry below. The score never weighed property type,
            # so a townhouse in a condo-heavy ZIP was handed eight single-family
            # comps and told by the CMA skill to require "same property type" --
            # leaving it to drop the whole set with nothing in `screened_out`
            # explaining why. Counted now, so a zero comp set reads as "no
            # townhouses have sold near here" rather than as a thin market.
            if same_property_type and candidate.property_type != subject.property_type:
                rejected["type_mismatch"] += 1
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

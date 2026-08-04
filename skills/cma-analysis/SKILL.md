---
name: cma-analysis
description: Comparative market analysis methodology for residential property — comp selection criteria, dollar-adjustment grids, reconciliation to a value range, and the CMA report structure. Use when valuing a property, recommending a list price, or building offer strategy.
---

# Comparative Market Analysis

## Overview

A CMA estimates value by adjusting recent comparable sales toward the subject
property. The comps set the market; the adjustments account for how the subject
differs from them.

## When to Use

Valuing a property, recommending a list price, advising on offer strategy, or
answering "is this priced correctly".

## Comp Selection

Rank candidates on these, in priority order:

1. **Proximity** — same neighbourhood beats same city. Start at 1.0–1.5 miles.
2. **Recency** — closed within 6 months. Beyond 12 months, apply a market-trend
   adjustment or drop the comp.
3. **Size** — within ±20% of subject square footage.
4. **Type and configuration** — same property type; bed count within ±1.
5. **Age and condition** — within ~15 years of the subject's build year.

**Minimums.** Three comps is the floor for a defensible CMA. On fewer, label
the output "preliminary indication" and say what would firm it up. Never pad
the set with poor comps to reach three — a two-comp CMA honestly labelled beats
a three-comp CMA with a bad third.

Exclude: distressed and foreclosure sales, arm's-length failures (family
transfers), and any sale whose price-per-sqft sits more than ~30% off the rest
of the set without an explanation you can name.

## Adjustment Grid

Adjust **the comp toward the subject**. Comp superior to subject → subtract.
Comp inferior → add.

| Feature | Typical adjustment | Direction |
|---|---|---|
| Living area | Local $/sqft × difference, damped to ~50% | Comp larger → subtract |
| Bedroom count | $5,000–$15,000 per bed | Comp has more → subtract |
| Bathroom count | $5,000–$10,000 per bath | Comp has more → subtract |
| Garage space | $5,000–$12,000 per bay | Comp has more → subtract |
| Lot size | Local land rate × difference | Comp larger → subtract |
| Age / condition | 0.5%–1% of value per 10 years | Comp newer → subtract |
| HOA burden | ~100× the monthly delta | Comp lower fee → subtract |

The living-area damping matters: a home twice the size is not worth twice as
much, so applying raw $/sqft to a large size gap overstates the adjustment.

The HOA multiplier is stated rather than left to judgement because the row is
often the largest single adjustment in condo and townhouse markets, and a
capitalisation basis you pick per comp is not a method. Where fees run high — a
$400/month delta is $40,000 of value at this basis — say the basis out loud in
your report so the number can be checked.

**Net vs gross.** Track both. If gross adjustments exceed ~25% of a comp's sale
price, that comp is too dissimilar — drop it and say why.

**Exempt the HOA row from that 25% test.** A large fee delta means the two
properties carry different ownership costs, not that they are different
properties: a same-building, same-size, same-vintage comp is the *best* comp
available even when its fee differs by $600/month. Include the HOA row in the
adjusted value and exclude it from the gross-adjustment total you screen on.
Without this the test drops the closest comps in exactly the markets where fees
matter most, and a CMA built from what survives looks defensible and is not.

## Reconciliation

Do **not** average the adjusted values. Weight them: the comps that needed the
smallest gross adjustment are the most reliable, and they should drive the
number. Use the same gross total the 25% screen uses — HOA excluded — or a
same-building comp gets kept by the screen and then down-weighted for the very
adjustment the screen just forgave.

Produce a **range**, then state where in that range you land and why. A single
unqualified figure implies a precision the method does not have.

Cross-check the range against `market_statistics`:

- Under ~4 months of inventory → seller's market, lean to the upper half.
- Over ~6 months → buyer's market, lean to the lower half.
- Rising median days-on-market → the market is softening; discount accordingly.

## Report Structure

Write to `/workspace/cma-<listing-id>.md`:

```markdown
# CMA — <address>

## Subject Property
<beds/baths, sqft, year built, current list price if any, $/sqft, HOA/mo>

## Comparable Sales
| Address | Sold | Price | $/sqft | Sqft | Beds | HOA/mo | Net adj. | Adjusted |
|---|---|---|---|---|---|---|---|---|

## Adjustments Applied
<Per comp: which features were adjusted, how much, and why.>

## Market Context
<Inventory, months of supply, median DOM, direction of travel.>

## Indicated Value
**Range: $X – $Y**  |  **Point estimate: $Z**
<Why Z sits where it does in the range.>

## Confidence and Limitations
<Comp count, data recency, anything you could not verify.>
```

## Guardrails

- A CMA is **not an appraisal**. Say so in the report. Recommend a licensed
  appraiser wherever the number carries legal or lending weight.
- Never adjust for, or comment on, the racial, ethnic, religious, national-origin,
  familial, or disability characteristics of a neighbourhood or its residents.
  Fair-housing law prohibits it and it has no valuation basis. Describe schools,
  amenities, and commute in factual terms only, and attribute any school rating
  to its source rather than characterising the school yourself.
- Show the arithmetic. An adjustment you cannot justify in one sentence should
  not be in the grid.

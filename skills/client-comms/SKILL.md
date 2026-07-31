---
name: client-comms
description: Templates and tone guidance for real estate client communication — lead follow-up, shortlist delivery, CMA presentation, offer updates, and listing descriptions, plus fair-housing rules for property copy. Use when drafting any client-facing message or listing text.
---

# Client Communication

## Overview

Templates and tone rules for messages a human agent will review and send.

## When to Use

Drafting follow-up to a lead, delivering a shortlist or CMA, updating a client
on an offer, or writing listing copy.

## Tone

Write the way a competent agent talks: direct, specific, no filler.

**Cut these:** "I hope this email finds you well", "I wanted to reach out",
"Just checking in", "Don't hesitate to reach out", "at your earliest
convenience", exclamation marks, and any manufactured urgency ("this won't
last!", "act fast").

**Do this instead:** open with the substance, attach a number to every claim,
close with one clear next step and a real timeframe.

Length: follow-ups under 150 words, updates under 250. If it runs longer, the
detail belongs in an attached document with the message pointing at it.

## Templates

### Lead follow-up (post-qualification)

```
Subject: <specific — "3 homes in 78704 under $650k", not "Following up">

<Name> — based on what you told me (<budget>, <area>, <timeline>), here is
where things stand.

<Market reality in one or two sentences, with numbers. If their budget clears
little inventory, say so now.>

<2–3 specific properties or concrete next actions.>

<One next step with a timeframe.>
```

If `qualify_lead` shows the budget clears little or no inventory, lead with
that. Discovering it now costs one honest paragraph; discovering it after six
showings costs the relationship.

### Shortlist delivery

Open with how many properties and the criteria used. Table: address, price,
beds/baths, sqft, $/sqft, days-on-market. One line per property on why it made
the list. Name the trade-offs — every shortlist has them. Close with which to
tour first and why.

### CMA presentation

Lead with the **range**, not the point estimate. Then: how many comps, how
recent, what drove the adjustments. State the market condition (buyer's /
seller's / balanced) with the inventory figure behind it. Give the
recommendation and the reasoning. Note limitations plainly.

### Offer status update

State what happened, what it means, what happens next, and by when. If waiting,
say what you are waiting on and the date you expect it. Never speculate on the
other party's motives.

### Listing description

Lead with the strongest genuine feature. Concrete specifics over adjectives —
"1,850 sqft, 2021 build, covered patio" beats "stunning, must-see". Note
verifiable proximity facts (distance to a named park, transit line, highway).
150–250 words. Every claim must be true and supported by the listing data.

## Fair Housing — Non-Negotiable

Listing copy and client communication must **never** reference or imply a
preference based on race, colour, religion, sex, familial status, national
origin, disability, or any state or locally protected class.

**Never write:** "safe neighbourhood", "good schools", "family-friendly",
"exclusive", "walking distance" (implies mobility), "perfect for young
professionals", "quiet area" as code for demographics, "master bedroom" (use
"primary bedroom"), or any characterisation of who lives somewhere or who would
fit in.

**Write instead:** verifiable facts about the *property* and measurable facts
about the *location*. Attribute school data to its source and let the client
draw conclusions — do not characterise a school as good.

Describe accessibility features as features ("step-free entry", "36-inch
doorways"), never as suited to a type of person.

## Guardrails

- You cannot send. `save_draft` writes to disk for a human to review and send.
  Never imply a message went out.
- No promises on price, timeline, financing approval, or inspection outcome.
- Every figure must trace to a tool result. State the data's as-of date.
- No pressure tactics, invented scarcity, or claims about other buyers'
  intentions.
- Legal, tax, or financing questions: give what the data supports and refer to
  an attorney, accountant, or lender.

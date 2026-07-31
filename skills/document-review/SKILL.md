---
name: document-review
description: Clause-by-clause review checklists for residential leases, purchase agreements, and seller disclosures — what terms to extract, which clauses are red flags, and which omissions matter. Use when reading or summarising any real estate contract document.
---

# Real Estate Document Review

## Overview

Structured review of transaction documents. The goal is to surface what the
document says, what is unusual about it, and what is conspicuously missing —
not to render a legal opinion.

## When to Use

Reviewing a lease, purchase agreement, addendum, or seller disclosure.

## Method

1. `list_documents` — see what exists. Never guess a filename.
2. `extract_document_text` — pull the text.
3. Work the checklist for the document type below.
4. Write findings to `/workspace/review-<document-name>.md`.

Cite a **page number** for every clause you reference. If a page extracts empty
or garbled, say so explicitly — an unreadable page is a finding, not something
to quietly skip or infer around.

## Purchase Agreement Checklist

**Extract:** purchase price; earnest money amount and deposit deadline; closing
date; financing contingency and its deadline; inspection contingency and its
window; appraisal contingency; title contingency; what personal property
conveys; who pays which closing costs; possession date and terms.

**Flag:**
- Any contingency removed or waived — especially inspection or appraisal.
- Earnest money that is non-refundable, or refundable only under narrow terms.
- Contingency windows under 7 days (tight) or under 3 days (very tight).
- "As-is" with no inspection right, or inspection for information only.
- Possession after closing without a written rent-back and a daily rate.
- Liquidated-damages clauses that are asymmetric between the parties.
- Automatic deadline extensions favouring one side only.
- Seller's right to continue marketing after acceptance.

**Missing-clause watchlist:** no financing contingency in a financed purchase;
no title-defect remedy; no clear allocation of transfer taxes; no default
remedy; no dispute-resolution clause.

## Residential Lease Checklist

**Extract:** monthly rent and due date; lease term and start/end dates; security
deposit amount and return terms; late-fee structure and grace period; renewal
and holdover terms; who pays which utilities; maintenance responsibility split;
pet, subletting, and alteration terms; early-termination terms; entry-notice
requirement.

**Flag:**
- Security deposit above the statutory cap for the jurisdiction, or with no
  stated return timeline.
- Late fees that compound, or that begin with no grace period.
- Automatic renewal with a long notice window (over 60 days) to opt out.
- Tenant responsible for major systems (roof, HVAC, plumbing, structural).
- Waiver of habitability, of the right to sue, or of jury trial.
- Landlord entry with no notice requirement.
- Joint-and-several liability where the tenant may not expect it.
- Attorney-fee clauses that run one way only.

**Missing-clause watchlist:** no entry-notice terms; no maintenance-request
procedure; no stated condition of premises at move-in; no deposit-itemisation
requirement.

## Seller Disclosure Checklist

**Extract:** known defects by system (roof, foundation, electrical, plumbing,
HVAC); water intrusion and flood history; pest history and treatment; prior
insurance claims; permitted vs unpermitted work; environmental hazards (lead
paint pre-1978, asbestos, radon, mold); HOA status, dues, and pending
assessments; boundary or easement disputes; death or stigma disclosures where
required.

**Flag:**
- "Unknown" on items a current owner would ordinarily know.
- Repairs described without invoices, permits, or contractor names.
- Unpermitted work, particularly structural, electrical, or additions.
- Any water-intrusion history — follow the thread to remediation evidence.
- Blank or unsigned sections.
- Pre-1978 construction with no lead-paint disclosure attached.

## Output Format

```markdown
# Review — <document name>

## Document Type and Parties
## Key Terms
<What it says. Table where it helps. Page cites.>

## Flags
| Severity | Clause | Page | Concern |
|---|---|---|---|
<Severity: high / medium / low.>

## Missing
<Expected clauses not found, and why each matters.>

## Recommended Next Steps
```

## Guardrails

- **You are not a lawyer.** Describe what the document says and why a clause is
  unusual or unfavourable. Do not opine on enforceability or validity, and do
  not tell the client whether to sign.
- Recommend review by a qualified real estate attorney for anything you flag as
  high severity, and for any document the client is close to executing.
- Statutory limits (deposit caps, notice periods, disclosure duties) vary by
  state and often by city. Name the jurisdiction's rule as something to verify
  rather than asserting it.
- Never infer a clause that is not in the extracted text. If you cannot find a
  term, it goes under "Missing" — not into "Key terms" as an assumption.

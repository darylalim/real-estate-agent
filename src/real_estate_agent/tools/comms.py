"""Client communication and lead qualification tools.

Deliberately no send capability. ``save_draft`` writes to disk and stops; a
human opens the file and sends it. If you later add real delivery, gate it with
``interrupt_on`` (see ``build_agent``) rather than letting the agent send
unattended.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, urlencode

from langchain.tools import tool
from langchain_core.tools import BaseTool

from real_estate_agent.config import DRAFTS_DIR
from real_estate_agent.providers.base import ListingsProvider

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_WHITESPACE_RUN = re.compile(r"\s+")

# Outlook truncates mailto: around 2 KB; stay clear of the edge.
_MAILTO_MAX_CHARS = 1800

# Purchase-price equivalent of $1/month of association fee. The same basis the
# cma-analysis skill states for its HOA adjustment row, deliberately: the
# analyst valuing a fee delta in a CMA and the liaison testing a budget against
# one are answering the same question, and two bases would let the same fee be
# worth two different amounts on one screen.
_HOA_CAPITALISATION = 100


def _collapse_whitespace(value: str) -> str:
    """Flatten a value destined for an email header to a single line."""
    return _WHITESPACE_RUN.sub(" ", value or "").strip()


def _unique_pair(base: str) -> tuple[Path, Path]:
    """Return (.md, .eml) paths where neither file already exists."""
    candidate = base
    suffix = 1
    while (DRAFTS_DIR / f"{candidate}.md").exists() or (
        DRAFTS_DIR / f"{candidate}.eml"
    ).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return DRAFTS_DIR / f"{candidate}.md", DRAFTS_DIR / f"{candidate}.eml"


def make_comms_tools(provider: ListingsProvider) -> list[BaseTool]:
    """Return the lead-qualification and drafting tools bound to ``provider``."""

    @tool
    def qualify_lead(
        name: str,
        target_city: str,
        budget_max: int,
        timeline_months: int,
        pre_approved: bool = False,
        min_beds: int | None = None,
        state: str | None = None,
    ) -> str:
        """Score an inbound lead and test their budget against real inventory.

        Combines a readiness tier (financing plus timeline) with a feasibility
        check: how much of the current market falls within their stated maximum
        LIST PRICE. A pre-approved buyer whose budget is below every listing in
        their target city is not a strong lead, and this surfaces that.

        Two feasibility counts come back, not one. The list-price screen answers
        "can they bid on it"; the fee-inclusive screen answers "can they carry
        it", capitalising `hoa_monthly` at the same basis the CMA methodology
        uses. Where fees are high the two diverge sharply and the cheapest
        listing by sticker is routinely not the cheapest to own — report the
        gap rather than either number alone.

        Args:
            name: Lead's name.
            target_city: City they want to buy in.
            budget_max: Maximum purchase price in whole dollars.
            timeline_months: How many months until they intend to close.
            pre_approved: Whether they hold a mortgage pre-approval.
            min_beds: Minimum bedrooms they require, if stated.
            state: Two-letter state code, if known.
        """
        # Three counts, because "budget clears X% of inventory" is only a
        # statement about budget if the denominator already satisfies every
        # other requirement. Dividing by total inventory blames the budget for
        # a bedroom shortfall.
        matches = list(
            provider.search(
                city=target_city,
                state=state,
                max_price=budget_max,
                min_beds=min_beds,
                status="active",
                limit=500,
            )
        )
        meets_requirements = list(
            provider.search(
                city=target_city,
                state=state,
                min_beds=min_beds,
                status="active",
                limit=500,
            )
        )
        # Computed here rather than described to the model, which is the repo's
        # standing rule: a share the model re-derives per turn from a paragraph
        # of prose is a share that can differ between two runs of the same
        # question. `_HOA_CAPITALISATION` matches the CMA skill's adjustment
        # basis, so the two halves of the app value a fee the same way.
        carryable = [
            listing
            for listing in matches
            if listing.price + (listing.hoa_monthly or 0) * _HOA_CAPITALISATION
            <= budget_max
        ]
        total_active = list(
            provider.search(city=target_city, state=state, status="active", limit=500)
        )

        # Readiness: financing is worth more than urgency.
        score = 0
        signals: list[str] = []
        if pre_approved:
            score += 45
            signals.append("Holds mortgage pre-approval.")
        else:
            signals.append("No pre-approval on file — financing is the gating step.")

        if timeline_months <= 3:
            score += 35
            signals.append(f"Near-term timeline ({timeline_months} months).")
        elif timeline_months <= 6:
            score += 22
            signals.append(f"Mid-term timeline ({timeline_months} months).")
        else:
            score += 8
            signals.append(f"Long timeline ({timeline_months} months) — nurture, don't push.")

        # Budget feasibility measured only against homes that already meet the
        # non-price requirements.
        share = (len(matches) / len(meets_requirements)) if meets_requirements else 0.0
        if not meets_requirements:
            signals.append(
                "No active listing meets the stated requirements at any price — the "
                "requirements, not the budget, are the binding constraint."
            )
        elif share >= 0.35:
            score += 20
            signals.append(
                f"Budget clears {share:.0%} of listings that meet their requirements."
            )
        elif share > 0:
            score += 10
            signals.append(
                f"Budget clears only {share:.0%} of listings that meet their "
                "requirements — expect a narrow search."
            )
        else:
            signals.append(
                "Budget clears none of the listings that meet their requirements — "
                "reset expectations or widen the area."
            )

        # Called out separately so a requirement-driven shortage is never
        # reported as a budget problem.
        if total_active and len(meets_requirements) / len(total_active) < 0.25:
            signals.append(
                f"Only {len(meets_requirements)} of {len(total_active)} active listings "
                "meet the non-price requirements — the requirements are narrowing the "
                "search more than the budget is."
            )

        tier = "hot" if score >= 75 else "warm" if score >= 45 else "cool"

        return json.dumps(
            {
                "lead": {
                    "name": name,
                    "target_city": target_city,
                    "state": state,
                    "budget_max": budget_max,
                    "timeline_months": timeline_months,
                    "pre_approved": pre_approved,
                    "min_beds": min_beds,
                },
                "qualification": {"score": score, "tier": tier, "signals": signals},
                "market_feasibility": {
                    "active_listings_in_city": len(total_active),
                    "listings_meeting_requirements": len(meets_requirements),
                    "listings_within_budget_and_requirements": len(matches),
                    "share_of_qualifying_inventory_within_list_price": round(share, 3),
                    "denominator": (
                        "listings_meeting_requirements — so this share reflects "
                        "budget alone, not the bedroom or location filters"
                    ),
                    "listings_within_budget_including_fees": len(carryable),
                    "share_of_qualifying_inventory_carryable": round(
                        (len(carryable) / len(meets_requirements))
                        if meets_requirements
                        else 0.0,
                        3,
                    ),
                    "fee_basis": (
                        f"list price + {_HOA_CAPITALISATION}× hoa_monthly, the "
                        "same capitalisation the CMA adjustment grid uses. The "
                        "gap between this count and "
                        "listings_within_budget_and_requirements is how much of "
                        "their apparent affordability the association fees take "
                        "back — lead with it when the two differ"
                    ),
                    "sample_matches": [listing.as_dict() for listing in matches[:5]],
                },
            },
            indent=2,
        )

    @tool
    def save_draft(filename: str, subject: str, body: str, to: str = "") -> str:
        """Save a client-facing draft and produce a one-click send handoff.

        This is the ONLY correct way to produce a draft — do not also write the
        message with `write_file`, or the reviewer gets two divergent copies of
        the same email and has to guess which is current.

        Writes three things and sends nothing:
          - a .md file, the readable canonical copy
          - a .eml file that opens in the reviewer's mail client as an
            editable unsent message
          - a mailto: URL in the return value, which opens a prefilled compose
            window in one click (omitted when the body is too long for a URL)

        A human still reads the text and presses send. Say so when you report
        back, and never imply a message went out.

        Args:
            filename: Base name, e.g. "follow-up-jane-doe". Extension and
                timestamp are added automatically.
            subject: Subject line.
            body: Full message body. Plain prose, not Markdown scaffolding —
                it goes straight into an email.
            to: Recipient address. Optional; leave empty if unknown and the
                reviewer will fill it in.
        """
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

        # Header values may not contain CR/LF — Python's email policy raises,
        # and a raw newline here is the classic header-injection vector. Model
        # output routinely carries a stray newline in a subject line.
        subject = _collapse_whitespace(subject)
        to = _collapse_whitespace(to)

        stem = _SAFE_FILENAME.sub("-", filename).strip("-.") or "draft"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

        # Second-resolution stamps collide when a draft is revised in the same
        # turn. Silently overwriting the earlier copy is the exact failure this
        # tool exists to prevent, so find a free name instead.
        md_path, eml_path = _unique_pair(f"{stem}-{stamp}")

        # mailto: is the genuine one-click path, but clients cap URL length
        # (Outlook truncates around 2 KB). Offer it only when it will survive.
        query = urlencode({"subject": subject, "body": body}, quote_via=quote)
        mailto = f"mailto:{quote(to)}?{query}"
        mailto_usable = len(mailto) <= _MAILTO_MAX_CHARS

        # Build the message first. If a header is rejected we must not have
        # already written a .md pointing at a .eml that never gets created.
        #
        # X-Unsent: 1 is the convention that makes a mail client open this as a
        # composable draft rather than a received message. No From and no Date:
        # the client fills those, and their absence keeps it clearly unsent.
        message = EmailMessage()
        if to:
            message["To"] = to
        message["Subject"] = subject
        message["X-Unsent"] = "1"
        message.set_content(body)
        rendered = message.as_bytes()

        md_path.write_text(
            f"# {subject}\n\n"
            f"**To:** {to or '_(fill in)_'}  \n"
            f"**Drafted:** {stamp}  \n"
            f"**Status:** not sent — review, then send\n\n"
            f"Open `{eml_path.name}` in your mail client to edit and send.\n\n"
            f"---\n\n{body}\n",
            encoding="utf-8",
        )
        eml_path.write_bytes(rendered)

        return json.dumps(
            {
                "sent": False,
                "note": "Nothing was sent. A human must review and send.",
                "markdown_path": str(md_path),
                "eml_path": str(eml_path),
                "open_command": f"open {eml_path}",
                "mailto_url": mailto if mailto_usable else None,
                "mailto_omitted_reason": (
                    None
                    if mailto_usable
                    else f"body too long for a mailto: URL ({len(mailto)} chars); use the .eml"
                ),
                "to": to or None,
                "subject": subject,
            },
            indent=2,
        )

    return [qualify_lead, save_draft]

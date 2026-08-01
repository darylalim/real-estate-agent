"""Client communication and lead qualification tools.

Deliberately no send capability. ``save_draft`` writes to disk and stops; a
human opens the file and sends it. If you later add real delivery, gate it with
``interrupt_on`` (see ``build_agent``) rather than letting the agent send
unattended.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import quote, urlencode

from langchain.tools import tool
from langchain_core.tools import BaseTool

from real_estate_agent.config import DRAFTS_DIR
from real_estate_agent.providers.base import ListingsProvider

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

# Outlook truncates mailto: around 2 KB; stay clear of the edge.
_MAILTO_MAX_CHARS = 1800


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
        check: how much of the current market actually matches what they can
        afford. A pre-approved buyer with a budget below every listing in their
        target city is not a strong lead, and this surfaces that.

        Args:
            name: Lead's name.
            target_city: City they want to buy in.
            budget_max: Maximum purchase price in whole dollars.
            timeline_months: How many months until they intend to close.
            pre_approved: Whether they hold a mortgage pre-approval.
            min_beds: Minimum bedrooms they require, if stated.
            state: Two-letter state code, if known.
        """
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

        share = (len(matches) / len(total_active)) if total_active else 0.0
        if share >= 0.35:
            score += 20
            signals.append("Budget clears a healthy share of active inventory.")
        elif share > 0:
            score += 10
            signals.append(
                f"Budget clears only {share:.0%} of active inventory — expect a narrow search."
            )
        else:
            signals.append(
                "Budget clears no current active inventory — reset expectations or widen the area."
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
                    "listings_within_budget": len(matches),
                    "share_of_inventory_affordable": round(share, 3),
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
        stem = _SAFE_FILENAME.sub("-", filename).strip("-.") or "draft"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        md_path = DRAFTS_DIR / f"{stem}-{stamp}.md"
        eml_path = DRAFTS_DIR / f"{stem}-{stamp}.eml"

        # mailto: is the genuine one-click path, but clients cap URL length
        # (Outlook truncates around 2 KB). Offer it only when it will survive.
        query = urlencode({"subject": subject, "body": body}, quote_via=quote)
        mailto = f"mailto:{quote(to)}?{query}"
        mailto_usable = len(mailto) <= _MAILTO_MAX_CHARS

        md_path.write_text(
            f"# {subject}\n\n"
            f"**To:** {to or '_(fill in)_'}  \n"
            f"**Drafted:** {stamp}  \n"
            f"**Status:** not sent — review, then send\n\n"
            f"Open `{eml_path.name}` in your mail client to edit and send.\n\n"
            f"---\n\n{body}\n",
            encoding="utf-8",
        )

        # X-Unsent: 1 is the convention that makes a mail client open this as a
        # composable draft rather than a received message. No From and no Date:
        # the client fills those, and their absence keeps it clearly unsent.
        message = EmailMessage()
        if to:
            message["To"] = to
        message["Subject"] = subject
        message["X-Unsent"] = "1"
        message.set_content(body)
        eml_path.write_bytes(message.as_bytes())

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

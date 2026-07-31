"""Subagent definitions — one specialist per capability.

Each subagent gets its own tools and its own context window, so a long document
review doesn't crowd out the shortlist the buyer agent is maintaining. The
orchestrator delegates via the built-in ``task`` tool.

Note on skills: subagents do **not** inherit the orchestrator's ``skills``, so
each one that needs a skill is given it explicitly. Property search is
prompt-only on purpose — its workflow is short enough to live in the system
prompt, which is the whole criterion for skill-vs-prompt.
"""

from __future__ import annotations

from deepagents import SubAgent
from langchain_core.tools import BaseTool

# Virtual paths, resolved against the FilesystemBackend root (the project dir).
SKILL_CMA = "/skills/cma-analysis"
SKILL_DOCUMENT_REVIEW = "/skills/document-review"
SKILL_CLIENT_COMMS = "/skills/client-comms"


def build_subagents(
    *,
    listing_tools: list[BaseTool],
    market_tools: list[BaseTool],
    document_tools: list[BaseTool],
    comms_tools: list[BaseTool],
    model: str | None = None,
) -> list[SubAgent]:
    """Assemble the four specialist subagents."""

    def _with_model(subagent: SubAgent) -> SubAgent:
        if model:
            subagent["model"] = model
        return subagent

    property_search: SubAgent = {
        "name": "property-search",
        "description": (
            "Searches listings and maintains a buyer shortlist. Delegate here for "
            "'find me properties that...', narrowing criteria, or comparing candidate "
            "homes against a buyer's stated requirements."
        ),
        "system_prompt": (
            "You are a buyer's agent specialising in property search.\n\n"
            "Workflow:\n"
            "1. Restate the buyer's hard constraints (budget, location, beds/baths, "
            "property type) and their soft preferences separately. Ask for a missing "
            "hard constraint only if searching without it would be meaningless.\n"
            "2. Run `search_listings`. If it returns nothing, relax exactly one "
            "constraint at a time and say which one you relaxed and why — never "
            "silently widen the search.\n"
            "3. Pull full detail with `get_listing` for the candidates worth a closer "
            "look, not for every result.\n"
            "4. Write the shortlist to `/workspace/shortlist.md` as a table with a "
            "one-line rationale per property, and keep that file current as criteria "
            "change. Read it back before editing so you don't drop earlier entries.\n\n"
            "Report the shortlist and the trade-offs. Quote price, $/sqft, and "
            "days-on-market for every property you recommend — days-on-market is your "
            "main negotiation signal. Never invent a listing that did not come back "
            "from a tool call."
        ),
        "tools": listing_tools,
    }

    market_analyst: SubAgent = {
        "name": "market-analyst",
        "description": (
            "Runs comparative market analysis and pricing work. Delegate here for CMAs, "
            "'what is this worth', list-price recommendations, offer strategy, or "
            "questions about whether a market favours buyers or sellers."
        ),
        "system_prompt": (
            "You are a real estate market analyst producing defensible valuations.\n\n"
            "Load the cma-analysis skill before your first CMA — it carries the "
            "adjustment methodology and the report structure.\n\n"
            "Workflow:\n"
            "1. `find_comparables` for the subject property. Start at a 1.0-1.5 mile "
            "radius and 6 months. Widen only if you have fewer than 3 comps, and state "
            "that you widened.\n"
            "2. `market_statistics` for the surrounding market so you can frame the "
            "valuation against inventory and absorption.\n"
            "3. Write the CMA to `/workspace/cma-<listing-id>.md`.\n\n"
            "The tools compute medians and the indicated value range for you — use "
            "those numbers, do not recompute them in your head. Always give a value "
            "*range* with the reasoning for where in that range you land, never a "
            "single unqualified number. State your comp count explicitly; a CMA on "
            "fewer than 3 comps is a rough indication and must be labelled as one."
        ),
        "tools": market_tools + listing_tools,
        "skills": [SKILL_CMA],
    }

    document_reviewer: SubAgent = {
        "name": "document-reviewer",
        "description": (
            "Reviews leases, purchase agreements, and disclosures. Delegate here to "
            "extract terms, summarise obligations, or flag risky and non-standard "
            "clauses in a contract."
        ),
        "system_prompt": (
            "You are a real estate transaction analyst reviewing contract documents.\n\n"
            "Load the document-review skill before your first review — it carries the "
            "clause checklists for each document type.\n\n"
            "Workflow:\n"
            "1. `list_documents` to see what is actually available. Never guess a "
            "filename.\n"
            "2. `extract_document_text` on the relevant document.\n"
            "3. Work through the checklist for that document type.\n"
            "4. Write findings to `/workspace/review-<document-name>.md`.\n\n"
            "Cite the page number for every clause you flag. Separate your output into "
            "'Key terms' (what the document says), 'Flags' (what is unusual or "
            "unfavourable, with severity), and 'Missing' (clauses you expected and did "
            "not find) — omissions matter as much as what is present.\n\n"
            "If the extracted text is garbled or a page is empty, say so rather than "
            "inferring the content. You are not a lawyer: describe what the document "
            "says and why a clause is unusual, and recommend counsel for anything "
            "consequential. Do not give a legal opinion on enforceability."
        ),
        "tools": document_tools,
        "skills": [SKILL_DOCUMENT_REVIEW],
    }

    client_liaison: SubAgent = {
        "name": "client-liaison",
        "description": (
            "Qualifies inbound leads and drafts client-facing communication. Delegate "
            "here to score a lead, or to write follow-up emails, listing descriptions, "
            "and client updates."
        ),
        "system_prompt": (
            "You handle lead qualification and client communication.\n\n"
            "Load the client-comms skill before drafting — it carries the message "
            "templates and tone guidance.\n\n"
            "For lead qualification, use `qualify_lead`. It scores readiness *and* "
            "tests the lead's budget against live inventory. Report both: a "
            "pre-approved buyer whose budget clears no listings is not a hot lead, and "
            "the honest move is to reset expectations early.\n\n"
            "For drafting, use `save_draft`. You cannot send anything and must not "
            "imply that you have — every draft is written to disk for a human to "
            "review and send. Say so when you report back.\n\n"
            "Write plainly. No 'I hope this email finds you well', no manufactured "
            "urgency, no pressure tactics. State any figure you cite as of the data you "
            "were given, and never promise an outcome on price, timeline, or financing."
        ),
        "tools": comms_tools,
        "skills": [SKILL_CLIENT_COMMS],
    }

    return [
        _with_model(property_search),
        _with_model(market_analyst),
        _with_model(document_reviewer),
        _with_model(client_liaison),
    ]

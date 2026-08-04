"""Tests that pin the behaviour easy to break by accident.

None of these call the Anthropic API. The agent test builds the graph with a
dummy key to assert on wiring only — the things that fail silently at runtime
(a missing planning tool, a permission rule in the wrong order) rather than
loudly at construction.
"""

from __future__ import annotations

import ast
import email
import email.policy
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from deepagents import FilesystemPermission
from deepagents.middleware.filesystem import _check_fs_permission
from langchain_core.tools import BaseTool
from langsmith.utils import tracing_is_enabled

from real_estate_agent.config import PROJECT_ROOT
from real_estate_agent.providers import MockListingsProvider
from real_estate_agent.providers.base import Listing
from real_estate_agent.providers.mock import PROPERTY_TYPES, _pool
from real_estate_agent.tools import comms, documents
from real_estate_agent.tools.comms import make_comms_tools
from real_estate_agent.tools.documents import make_document_tools
from real_estate_agent.tools.listings import make_listing_tools
from real_estate_agent.tools.market import make_market_tools


@pytest.fixture
def provider() -> MockListingsProvider:
    return MockListingsProvider()


@pytest.fixture
def comms_tools(tmp_path, monkeypatch) -> dict[str, BaseTool]:
    """Comms tools whose drafts land in a temp dir instead of the real workspace.

    Patching `comms.DRAFTS_DIR` only works because `save_draft` reads that global
    at call time. Bind it into a default argument or a local alias and this
    fixture silently stops working — the tests keep passing while writing into
    the real `workspace/drafts/`. `test_save_draft_sanitises_filename` asserts on
    `tmp_path` for exactly that reason, so the failure stays loud.

    Request `tmp_path` alongside this fixture to assert on paths; pytest hands
    both the same directory.
    """
    monkeypatch.setattr(comms, "DRAFTS_DIR", tmp_path)
    return {tool.name: tool for tool in comms.make_comms_tools(MockListingsProvider())}


def _subject_with_comps(provider: MockListingsProvider, minimum: int = 3):
    """A subject that has a usable comp set.

    Picking "the first active listing" couples the test to sort order and to
    whichever property happens to be a size outlier that day.
    """
    for listing in provider.search(status="active", limit=500):
        if len(provider.comparables(listing.listing_id)) >= minimum:
            return listing
    raise AssertionError(f"fixture contains no subject with >= {minimum} comps")


# --- provider -------------------------------------------------------------


def test_dataset_is_deterministic() -> None:
    """Two instances must agree, or prompt tuning chases phantom changes."""
    first = [listing.as_dict() for listing in MockListingsProvider().search(limit=500)]
    second = [listing.as_dict() for listing in MockListingsProvider().search(limit=500)]
    assert first == second
    assert first, "dataset should not be empty"


def _advertised_enum(tool: BaseTool, field: str) -> list[str]:
    """The values `search_listings` offers the model for ``field``.

    Read from the generated JSON schema rather than the Python annotation,
    because the schema is the thing the model is actually shown. `X | None`
    renders as an `anyOf` of the enum and null.
    """
    schema = cast(Any, tool.args_schema).model_json_schema()
    for branch in schema["properties"][field]["anyOf"]:
        if "enum" in branch:
            return branch["enum"]
    raise AssertionError(f"{field} is no longer an enum in the tool schema")


@pytest.mark.parametrize("field", ["status", "property_type"])
def test_every_advertised_filter_value_exists_in_the_dataset(
    provider: MockListingsProvider, field: str
) -> None:
    """A filter value the tool offers but the data never holds reads as a dead market.

    `search_listings` enumerates status as active/pending/sold, so the model can
    and will filter on any of them. At seed 1337 the original 0.45-0.50 pending
    band never fired — 40 active, 26 sold, 0 pending — so `status="pending"`
    returned an empty list forever. Nothing raised; the tool simply described a
    market with no homes under contract, which is a market signal the analyst
    would have reported.

    Asserted against the tool's own schema rather than a copied list, so adding
    a value to either Literal without adding data fails here.
    """
    tool = {t.name: t for t in make_listing_tools(provider)}["search_listings"]
    # The filter name is the parameter under test, so the call has to be built
    # dynamically; ty cannot match a computed keyword against the signature.
    # status=None explicitly: the provider defaults to active-only, so the
    # property_type leg would otherwise assert something narrower than its name
    # and could fail for a reason that is not the advertised defect. Note this
    # checks each value exists *somewhere*; a three-way combination such as
    # city + status + property_type can still be empty in a fixture this size,
    # and no reasonable dataset size fixes that.
    search = cast(Any, provider.search)
    for value in _advertised_enum(tool, field):
        assert search(**{field: value, "status": value if field == "status" else None},
                      limit=500), (
            f"no listing with {field}={value!r}, but search_listings offers it"
        )


def test_no_listing_carries_an_unadvertised_property_type(
    provider: MockListingsProvider,
) -> None:
    """The reverse of the test above, which only checked the forward direction.

    Each market's mix is built by `_pool(condo=18, townhouse=2)`, so the type
    names are *keyword arguments*. `_pool(single_famliy=17, ...)` type-checks,
    lints, and mints seventeen listings under a name `search_listings` never
    offers — the model could not filter for them, the Streamlit selectbox would
    show the typo, and the forward test stays green because every advertised
    value still exists. `_pool` raises on an unknown key; this asserts the same
    property against the generated data, which is what actually reaches anyone.
    """
    tool = {t.name: t for t in make_listing_tools(provider)}["search_listings"]
    advertised = set(_advertised_enum(tool, "property_type"))
    assert set(PROPERTY_TYPES) == advertised, (
        "the mock's canonical type list and the tool's Literal have drifted"
    )
    in_data = {listing.property_type for listing in provider.search(status=None, limit=500)}
    assert in_data <= advertised, f"unadvertised property types in the data: {in_data - advertised}"

    with pytest.raises(ValueError, match="not advertised"):
        _pool(single_famliy=3)


def test_search_respects_filters(provider: MockListingsProvider) -> None:
    results = provider.search(
        city="Hilo", max_price=600_000, min_beds=3, status="active"
    )
    assert results
    for listing in results:
        assert listing.city == "Hilo"
        assert listing.price <= 600_000
        assert listing.beds >= 3
        assert listing.status == "active"


def test_expensive_market_can_return_nothing(provider: MockListingsProvider) -> None:
    """The two markets are priced apart on purpose.

    Honolulu 96815/96816 runs well above Hilo, so a modest budget clears no
    Honolulu inventory. That asymmetry is what makes `qualify_lead`'s feasibility
    check meaningful — an empty result is a real answer, not a bug.

    $550k is "modest" on these islands, not the mainland figure it replaced:
    Honolulu's cheapest 3-bed is well over $800k. The threshold is deliberately
    clear of the boundary on both sides — Hilo returns several here, not one, so
    a later retune of the price model fails this on the property it is about
    rather than on a single listing drifting across the line.
    """
    assert provider.search(city="Honolulu", max_price=550_000, min_beds=3) == []
    assert provider.search(city="Hilo", max_price=550_000, min_beds=3)


def test_unknown_listing_returns_none(provider: MockListingsProvider) -> None:
    assert provider.get("MLS-does-not-exist") is None


def test_comparables_are_sold_and_exclude_subject(provider: MockListingsProvider) -> None:
    """A CMA on active listings or on the subject itself would be wrong."""
    subject = _subject_with_comps(provider)
    comps = provider.comparables(subject.listing_id, radius_miles=1.5, months_back=6)
    assert comps, "expected comps at the documented default radius"
    for comp in comps:
        assert comp.status == "sold"
        assert comp.sold_price is not None
        assert comp.listing_id != subject.listing_id


def test_most_listings_have_a_usable_comp_set(provider: MockListingsProvider) -> None:
    """A fixture where no CMA is ever possible cannot exercise the analyst.

    Regression: the size screen initially rejected everything because square
    footage was spread too widely across the dataset.
    """
    active = provider.search(status="active", limit=500)
    with_min_comps = sum(
        1 for listing in active if len(provider.comparables(listing.listing_id)) >= 3
    )
    assert with_min_comps / len(active) >= 0.6


# --- market tools ---------------------------------------------------------


def test_comparables_enforce_the_size_screen(provider: MockListingsProvider) -> None:
    """Regression: the live ladder returned comps 61-175% larger than subject.

    The CMA skill discards anything outside +/-20%, so handing those back only
    burned tokens on adjustments that were thrown away.
    """
    subject = _subject_with_comps(provider)

    for comp in provider.comparables(subject.listing_id):
        delta = abs(comp.sqft - subject.sqft) / subject.sqft
        assert delta <= 0.30, f"{comp.listing_id} is {delta:.0%} off subject size"

    # Disabling the screen must remain possible and must never narrow the set.
    screened = provider.comparables(subject.listing_id)
    unscreened = provider.comparables(subject.listing_id, max_sqft_delta_pct=None)
    assert len(unscreened) >= len(screened)


def test_comparables_report_what_was_screened_out(provider: MockListingsProvider) -> None:
    """A thin comp set and a thin market need to be distinguishable."""
    subject = _subject_with_comps(provider)
    # limit high enough that nothing is truncated, so the buckets must balance.
    _s, comps, rejected = provider.comparables_with_diagnostics(
        subject.listing_id, limit=1000
    )

    assert set(rejected) == {
        "not_sold",
        "stale",
        "type_mismatch",
        "outside_radius",
        "size_mismatch",
    }
    assert sum(rejected.values()) > 0

    # Every other listing is either returned or lands in exactly one bucket.
    # `status=None` means "any status"; the default is active-only.
    total = len(provider.search(status=None, limit=1000))
    assert len(comps) + sum(rejected.values()) == total - 1


def test_find_comparables_surfaces_screening(provider: MockListingsProvider) -> None:
    subject = _subject_with_comps(provider)
    tools = {tool.name: tool for tool in make_market_tools(provider)}
    payload = json.loads(
        tools["find_comparables"].invoke({"listing_id": subject.listing_id})
    )
    assert set(payload["screened_out"]) == {
        "not_sold",
        "stale",
        "type_mismatch",
        "outside_radius",
        "size_mismatch",
    }
    assert payload["search"]["max_sqft_delta_pct"] == 0.30
    assert payload["search"]["same_property_type"] is True
    # Every comp the tool hands back is the subject's own type by default. The
    # scorer never weighed type, so before the screen a townhouse in a
    # condo-heavy ZIP got a full set of single-family comps and nothing in
    # `screened_out` said so.
    subject_type = payload["subject"]["property_type"]
    assert all(comp["property_type"] == subject_type for comp in payload["comparables"])


def test_find_comparables_reports_value_range(provider: MockListingsProvider) -> None:
    tools = {tool.name: tool for tool in make_market_tools(provider)}
    subject = _subject_with_comps(provider)

    payload = json.loads(
        tools["find_comparables"].invoke({"listing_id": subject.listing_id})
    )
    value_range = payload["indicated_value_range"]
    assert value_range is not None
    assert value_range["low"] <= value_range["midpoint"] <= value_range["high"]
    assert payload["comp_statistics"]["count"] == len(payload["comparables"])


def test_market_statistics_handles_empty_market(provider: MockListingsProvider) -> None:
    """An unknown city must return nulls, not raise on statistics.median([])."""
    tools = {tool.name: tool for tool in make_market_tools(provider)}
    payload = json.loads(tools["market_statistics"].invoke({"city": "Nowhere"}))
    assert payload["active_inventory"]["count"] == 0
    assert payload["active_inventory"]["median_price"] is None


# --- document tools -------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["../../.env", "../pyproject.toml", "/etc/passwd", "nested/../../../.env"],
)
def test_document_extraction_rejects_traversal(path: str) -> None:
    """Filenames come from model output; escaping the documents dir must fail."""
    tools = {tool.name: tool for tool in make_document_tools()}
    payload = json.loads(tools["extract_document_text"].invoke({"filename": path}))
    assert "error" in payload
    assert "outside" in payload["error"] or "does not exist" in payload["error"]


def test_document_extraction_reads_a_real_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(documents, "DOCUMENTS_DIR", tmp_path)
    (tmp_path / "lease.md").write_text("Rent is $2,400 per month.", encoding="utf-8")

    tools = {tool.name: tool for tool in documents.make_document_tools()}
    payload = json.loads(tools["extract_document_text"].invoke({"filename": "lease.md"}))
    assert "2,400" in payload["text"]


def test_search_is_case_insensitive(provider: MockListingsProvider) -> None:
    """Regression: "Active" returned zero, which reads as an empty market."""
    assert len(provider.search(city="Honolulu", status="Active")) == len(
        provider.search(city="Honolulu", status="active")
    )
    assert len(provider.search(city="HONOLULU", property_type="Condo")) == len(
        provider.search(city="honolulu", property_type="condo")
    )


def test_market_statistics_window_filters_the_numerator(
    provider: MockListingsProvider,
) -> None:
    """Regression: months_back divided but never filtered, so the same 16 sales
    produced months-of-inventory of 5.2 or 21.0 purely by changing the window."""
    tools = {tool.name: tool for tool in make_market_tools(provider)}
    short = json.loads(
        tools["market_statistics"].invoke({"city": "Honolulu", "months_back": 3})
    )
    long = json.loads(
        tools["market_statistics"].invoke({"city": "Honolulu", "months_back": 12})
    )
    assert short["closed_sales"]["count"] < long["closed_sales"]["count"]
    assert short["market"]["closed_sales_window_months"] == 3


def test_sold_within_months_excludes_unsold(provider: MockListingsProvider) -> None:
    recent = provider.search(status="sold", sold_within_months=3, limit=500)
    assert recent
    assert all(listing.sold_date is not None for listing in recent)
    assert len(recent) < len(provider.search(status="sold", limit=500))


def test_lot_size_is_never_negative(provider: MockListingsProvider) -> None:
    assert all(listing.lot_sqft >= 0 for listing in provider.search(status=None, limit=500))


# --- lead qualification ---------------------------------------------------


def _listing(listing_id: str, price: int, beds: int, hoa_monthly: int = 0) -> Listing:
    """A minimal listing; only the fields `qualify_lead` reads vary."""
    return Listing(
        listing_id=listing_id, address=f"{listing_id} Test St", city="Testville",
        state="HI", zip_code="96815", price=price, beds=beds, baths=2.0, sqft=1200,
        lot_sqft=0, year_built=2000, property_type="condo", status="active",
        days_on_market=10, latitude=21.0, longitude=-157.0, hoa_monthly=hoa_monthly,
    )


class _FixedInventory:
    """A hand-built market satisfying the slice of `ListingsProvider` comms uses.

    This test is about `qualify_lead`'s arithmetic, not about the mock fixture,
    and against the fixture it kept breaking for reasons that were not the
    behaviour: the signal fires at `meets_requirements / total_active < 0.25`,
    and a 12-listing market moves that ratio in steps of 0.083, so every
    regeneration landed some pairing on or across the line. Twice. A market
    built here states the ratio outright and cannot drift.
    """

    def __init__(self, listings: list[Listing]) -> None:
        self._listings = listings

    def search(self, **filters: Any) -> list[Listing]:
        max_price = filters.get("max_price")
        min_beds = filters.get("min_beds")
        return [
            listing
            for listing in self._listings
            if (max_price is None or listing.price <= max_price)
            and (min_beds is None or listing.beds >= min_beds)
        ][: filters.get("limit", 25)]


def test_qualify_lead_does_not_blame_budget_for_a_bedroom_shortfall() -> None:
    """Regression: a $2M budget in a market it clears outright was reported as 8%.

    One 5-bed among nine 3-beds, every one far inside the budget. So the budget
    is not the binding constraint by construction — 1 of 10 meets the bedroom
    requirement and the budget clears all of it — and any report blaming the
    budget is the defect, not a property of the data.
    """
    provider = cast(
        Any,
        _FixedInventory(
            [_listing(f"MLS-{i}", 400_000, 3) for i in range(9)]
            + [_listing("MLS-9", 500_000, 5)]
        ),
    )
    tools = {tool.name: tool for tool in make_comms_tools(provider)}
    payload = json.loads(
        tools["qualify_lead"].invoke(
            {
                "name": "Jane",
                "target_city": "Testville",
                "budget_max": 2_000_000,
                "timeline_months": 2,
                "pre_approved": True,
                "min_beds": 5,
            }
        )
    )
    feasibility = payload["market_feasibility"]
    # Budget clears everything that meets the requirements; beds are binding.
    assert feasibility["share_of_qualifying_inventory_within_list_price"] == 1.0
    # The key says what it measures. It was "..._affordable" while measuring list
    # price alone, which is prompt surface making a claim the number cannot back.
    # Keys only — the payload's own guidance text uses the word deliberately,
    # telling the model to check `hoa_monthly` before calling anything affordable.
    assert not any("affordable" in key for key in feasibility)
    assert not any(
        "Budget clears only" in signal for signal in payload["qualification"]["signals"]
    )
    assert any(
        "requirements are narrowing" in signal
        for signal in payload["qualification"]["signals"]
    )


# --- draft handoff --------------------------------------------------------


def test_save_draft_survives_a_newline_in_the_subject(comms_tools) -> None:
    """Regression: a header newline raised *after* the .md was written, leaving
    an orphan pointing at a .eml that never existed. Also blocks header injection."""
    payload = json.loads(
        comms_tools["save_draft"].invoke(
            {
                "filename": "injected",
                "subject": "Shortlist\nBcc: attacker@evil.com",
                "body": "Body text.",
                "to": "jane@example.com",
            }
        )
    )
    eml = Path(payload["eml_path"])
    assert Path(payload["markdown_path"]).exists() and eml.exists()

    # The newline is collapsed, so the text survives *inside* the Subject value
    # rather than becoming a header of its own. Parse rather than substring-match:
    # "Bcc:" appearing in the subject text is harmless; a real Bcc header is not.
    message = email.message_from_bytes(eml.read_bytes(), policy=email.policy.default)
    assert message["Bcc"] is None, "must not have created a real Bcc header"
    assert "\n" not in str(message["Subject"])
    assert "attacker@evil.com" in str(message["Subject"])


def test_save_draft_does_not_clobber_same_second_drafts(comms_tools) -> None:
    """Regression: second-resolution stamps silently overwrote a revision."""
    args = {"filename": "follow-up", "subject": "One", "body": "First version."}
    first = json.loads(comms_tools["save_draft"].invoke(args))
    second = json.loads(
        comms_tools["save_draft"].invoke({**args, "body": "Second version."})
    )

    assert first["markdown_path"] != second["markdown_path"]
    assert "First version." in Path(first["markdown_path"]).read_text()
    assert "Second version." in Path(second["markdown_path"]).read_text()


def test_save_draft_emits_md_eml_and_mailto(comms_tools) -> None:
    payload = json.loads(
        comms_tools["save_draft"].invoke(
            {
                "filename": "follow-up-jane",
                "subject": "Hilo shortlist",
                "body": "Six listings match your criteria.",
                "to": "jane@example.com",
            }
        )
    )

    assert payload["sent"] is False
    md = Path(payload["markdown_path"])
    eml = Path(payload["eml_path"])
    assert md.exists() and eml.exists()

    raw = eml.read_text(encoding="utf-8")
    assert "To: jane@example.com" in raw
    assert "Subject: Hilo shortlist" in raw
    # Makes a mail client open this as an editable draft, not a received message.
    assert "X-Unsent: 1" in raw
    # Absence of From/Date is deliberate — it keeps the message clearly unsent.
    assert "\nFrom:" not in raw

    assert payload["mailto_url"].startswith("mailto:jane%40example.com?")
    assert "Hilo%20shortlist" in payload["mailto_url"]


def test_save_draft_omits_mailto_when_body_too_long(comms_tools) -> None:
    """mailto: silently truncates in real clients, so degrade explicitly."""
    payload = json.loads(
        comms_tools["save_draft"].invoke(
            {
                "filename": "long",
                "subject": "Long one",
                "body": "word " * 1000,
                "to": "jane@example.com",
            }
        )
    )
    assert payload["mailto_url"] is None
    assert "too long" in payload["mailto_omitted_reason"]
    assert Path(payload["eml_path"]).exists(), "the .eml must still be the fallback"


def test_save_draft_sanitises_filename(comms_tools, tmp_path) -> None:
    """Also the canary for the fixture's monkeypatch: if patching DRAFTS_DIR ever
    stops taking effect, drafts land in the real workspace and this fails."""
    payload = json.loads(
        comms_tools["save_draft"].invoke(
            {"filename": "../../escape/attempt", "subject": "s", "body": "b"}
        )
    )
    assert Path(payload["markdown_path"]).parent == tmp_path


# --- permission containment ----------------------------------------------


def _rules() -> list[FilesystemPermission]:
    """The agent's real rules, imported not copied.

    A hand-mirrored duplicate cannot fail when someone reorders the live list,
    which is precisely the order-sensitive hazard this test exists to catch.
    """
    from real_estate_agent.agent import WORKSPACE_PERMISSIONS

    return WORKSPACE_PERMISSIONS


@pytest.mark.parametrize(
    ("operation", "path", "expected"),
    [
        ("write", "/workspace/shortlist.md", "allow"),
        ("write", "/workspace/drafts/follow-up.md", "allow"),
        ("write", "/src/real_estate_agent/agent.py", "deny"),
        ("write", "/pyproject.toml", "deny"),
        ("write", "/.env", "deny"),
        ("read", "/.env", "deny"),
        ("read", "/skills/cma-analysis/SKILL.md", "allow"),
        # The checkpoint store sits inside /workspace/ because that is
        # gitignored, but /workspace/ is also the one subtree the agent can
        # write. Without a deny ahead of that allow, the model can truncate its
        # own history, and read_file on the db returns every other thread's
        # transcript. The sidecars matter as much: clobbering -wal corrupts
        # just as thoroughly as clobbering the db.
        ("write", "/workspace/checkpoints.db", "deny"),
        ("read", "/workspace/checkpoints.db", "deny"),
        ("write", "/workspace/checkpoints.db-wal", "deny"),
        ("write", "/workspace/checkpoints.db-shm", "deny"),
    ],
)
def test_permission_matrix(operation: str, path: str, expected: str) -> None:
    op = cast(Literal["read", "write"], operation)
    assert _check_fs_permission(_rules(), op, path) == expected


# --- approval gate (CLI) --------------------------------------------------


def test_action_requests_flattens_every_pending_call() -> None:
    """One decision per hanging tool call, or the middleware rejects the resume."""
    import main

    class FakeInterrupt:
        def __init__(self, value):
            self.value = value

    interrupts = [
        FakeInterrupt({"action_requests": [{"name": "save_draft", "args": {}}] * 2}),
        FakeInterrupt({"action_requests": [{"name": "save_draft", "args": {}}]}),
    ]
    assert len(main._action_requests(interrupts)) == 3


def test_prompt_returns_none_instead_of_raising(monkeypatch) -> None:
    """Regression: EOF on piped stdin crashed the CLI with a traceback."""
    import main

    def boom(_prompt_text):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    assert main._prompt("Approve? ") is None


def test_resume_payload_is_a_mapping_not_a_list() -> None:
    """Regression: the middleware reads `interrupt(request)["decisions"]`.

    A bare list raised `TypeError: list indices must be integers` the moment a
    reviewer answered, making --require-approval unusable in both directions.
    """
    import inspect

    import main

    source = inspect.getsource(main._handle_interrupts)
    assert 'Command(resume={"decisions": decisions})' in source
    assert "Command(resume=decisions)" not in source


def test_a_thread_survives_a_rebuild(tmp_path, monkeypatch) -> None:
    """`--thread <id>` has to outlive the process, or the flag is decoration.

    `build_agent` falls back to `InMemorySaver`, and for a long time `main.py`
    took that default — so resuming an id from an earlier run silently started
    an empty conversation and only the printed id suggested otherwise. Two
    separate savers over one file is exactly what two runs of the CLI do.

    Also pins `_already_rendered`: on resume the stored messages replay through
    `stream_mode="values"`, and anything it fails to key is printed a second
    time.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-construction-only")
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.sqlite import SqliteSaver

    import main
    from real_estate_agent.agent import build_agent

    db = tmp_path / "checkpoints.db"
    config = {"configurable": {"thread_id": "thread-under-test"}}

    with SqliteSaver.from_conn_string(str(db)) as saver:
        agent = build_agent(checkpointer=saver)
        agent.update_state(
            config, {"messages": [HumanMessage("an earlier turn", id="msg-1")]}
        )

    with SqliteSaver.from_conn_string(str(db)) as saver:
        rebuilt = build_agent(checkpointer=saver)
        stored = rebuilt.get_state(config).values["messages"]
        assert [message.content for message in stored] == ["an earlier turn"]
        assert main._already_rendered(rebuilt, config) == {"msg-1"}


def test_a_fresh_thread_has_nothing_to_replay(tmp_path, monkeypatch) -> None:
    """`_already_rendered` must be empty for an unknown id, not raise.

    Every run without `--thread` takes this path, so a getattr slip here breaks
    the common case rather than the resume case.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-construction-only")
    from langgraph.checkpoint.sqlite import SqliteSaver

    import main
    from real_estate_agent.agent import build_agent

    with SqliteSaver.from_conn_string(str(tmp_path / "empty.db")) as saver:
        agent = build_agent(checkpointer=saver)
        assert main._already_rendered(agent, {"configurable": {"thread_id": "new"}}) == set()


def test_cli_builds_with_a_durable_checkpointer() -> None:
    """`build_agent`'s InMemorySaver default is right for tests, wrong for the CLI.

    The two tests above construct their own SqliteSaver and pass it in, so they
    pass even if `main.py` drops back to the default -- at which point `--thread`
    silently resumes nothing again and the suite stays green. Asserted on source
    text, the same idiom as `test_resume_payload_is_a_mapping_not_a_list`.
    """
    import inspect

    import main

    source = inspect.getsource(main.main)
    assert "SqliteSaver.from_conn_string(str(CHECKPOINT_DB))" in source
    assert "checkpointer=checkpointer" in source


# Both fail-closed gates -- the CLI's and the chat page's -- are asserted on the
# syntax tree rather than the source text, because reaching either real branch
# needs a graph paused mid-tool-call, which needs a model call. A tree is the
# right shape for the question they ask ("is this statement inside that block")
# and, unlike a bounded `source.split(marker, 1)[0]`, has no marker that can go
# missing and silently widen the search to the rest of the file.


def _sole_guard(scope: ast.AST, *words: str) -> ast.If:
    """The one `if` under `scope` whose condition mentions every one of `words`.

    Matching on identifiers rather than a rendered string so that reformatting
    the condition -- reordering `and` operands, wrapping a long line -- does not
    count as a change in what it guards.
    """
    found = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.If)
        and set(words) <= set(re.findall(r"\w+", ast.unparse(node.test)))
    ]
    assert len(found) == 1, (
        f"expected exactly one guard whose condition mentions {words}, found {len(found)}"
    )
    return found[0]


def _function_named(path: Path, name: str) -> ast.FunctionDef:
    """One top-level function's tree, read from the file rather than imported."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} has no top-level `def {name}`")


def _sole_call(scope: ast.AST, dotted: str) -> ast.Call:
    """The one call to `dotted` under `scope`, as a node.

    Returning the node rather than a bool is what lets a caller ask where it
    *sits* -- `any(node is found for ...)` over a guard's body is an identity
    check, so it cannot be satisfied by a second, similar-looking call
    elsewhere in the function.
    """
    found = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == dotted
    ]
    assert len(found) == 1, f"expected exactly one `{dotted}` call, found {len(found)}"
    return found[0]


def _keyword_constant(call: ast.Call, name: str) -> Any:
    """One keyword argument's literal value, or None when it is not passed."""
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def test_resuming_a_pending_approval_without_the_flag_refuses() -> None:
    """The gate must not fail open across processes.

    With a durable checkpointer an approval outlives the process that asked for
    it, but the gate is only in the middleware stack when `--require-approval`
    is passed. Resuming such a thread without the flag would hand the graph a
    still-pending `save_draft` and nothing left to stop it, so `main` refuses.

    The third assertion is the one the previous version of this test described
    but never checked: the refusal has to come *before* the turn is sent.
    Draining the interrupt from inside `_turn` is too late — `_pump` has already
    put the new message on the graph by then — so ordering is the invariant, not
    merely the presence of a guard somewhere in the function.
    """
    main_fn = _function_named(PROJECT_ROOT / "main.py", "main")

    pending = _sole_guard(main_fn, "_pending_approvals")
    no_flag = _sole_guard(pending, "args", "require_approval")

    assert any(
        isinstance(statement, ast.Return)
        and statement.value is not None
        and ast.unparse(statement.value) == "1"
        for statement in no_flag.body
    ), "the no-flag path must exit non-zero rather than fall through to a turn"

    turns = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_turn"
    ]
    assert turns, "main no longer sends a turn; this test is checking nothing"
    assert pending.lineno < min(call.lineno for call in turns), (
        "the pending-approval check must run before any turn is sent, not after"
    )


# --- agent wiring ---------------------------------------------------------


def test_agent_exposes_planning_and_delegation(monkeypatch) -> None:
    """`write_todos` is not automatic in 0.7.1 — the orchestrator prompt needs it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-construction-only")
    from real_estate_agent.agent import build_agent

    agent = build_agent()
    names: set[str] = set()

    def walk(obj: object, depth: int = 0) -> None:
        if depth > 4:
            return
        for attr in ("tools_by_name", "_tools_by_name", "tools"):
            value = getattr(obj, attr, None)
            if isinstance(value, dict):
                names.update(value.keys())
            elif isinstance(value, (list, tuple)):
                names.update(getattr(item, "name", str(item)) for item in value)
        for attr in ("bound", "runnable", "func", "_func", "node"):
            value = getattr(obj, attr, None)
            if value is not None and value is not obj:
                walk(value, depth + 1)

    walk(agent.nodes.get("tools"))
    assert "write_todos" in names
    assert "task" in names
    assert {"read_file", "write_file", "ls"} <= names


def test_subagents_carry_their_own_skills() -> None:
    """Skills are not inherited from the orchestrator; each must declare its own."""
    from real_estate_agent.subagents import build_subagents

    subagents = build_subagents(
        listing_tools=[], market_tools=[], document_tools=[], comms_tools=[]
    )
    assert {sub["name"] for sub in subagents} == {
        "property-search",
        "market-analyst",
        "document-reviewer",
        "client-liaison",
    }
    by_name = {sub["name"]: sub for sub in subagents}
    assert by_name["market-analyst"]["skills"] == ["/skills/cma-analysis"]
    assert by_name["document-reviewer"]["skills"] == ["/skills/document-review"]
    assert by_name["client-liaison"]["skills"] == ["/skills/client-comms"]


# --- streamlit app --------------------------------------------------------
#
# `streamlit_app.py` is a second consumer of the package, alongside `main.py`,
# and it necessarily repeats two things the CLI already does. Both fail quietly
# rather than loudly, which is the criterion for pinning them here.


def test_streamlit_keys_messages_exactly_as_the_cli_does() -> None:
    """One dedupe key, two renderers.

    `stream_mode="values"` re-emits the entire message list on every chunk, so
    anything rendering a stream has to suppress repeats. The CLI and the app
    share threads through one checkpoint database — if the keys ever disagree, a
    thread started in one reprints its whole history in the other, and nothing
    raises.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    import main
    from ui.agent_session import message_key

    for message in (HumanMessage("an earlier turn", id="msg-1"), AIMessage("no id here")):
        assert message_key(message) == main._message_key(message)


def test_the_workspace_browser_never_offers_the_checkpoint_store(
    tmp_path, monkeypatch
) -> None:
    """The sidebar lists workspace files and wires them to a download button.

    `checkpoints.db` lives under `workspace/` and holds every thread's full
    transcript — which is precisely why `WORKSPACE_PERMISSIONS` denies the agent
    read and write on it. A file browser is the same disclosure by another
    route, so the allow-list has to keep excluding it, SQLite sidecars included.
    """
    from ui import agent_session

    monkeypatch.setattr(agent_session, "WORKSPACE_DIR", tmp_path)
    (tmp_path / "drafts").mkdir()
    (tmp_path / "shortlist.md").write_text("visible", encoding="utf-8")
    (tmp_path / "drafts" / "follow-up.eml").write_text("visible", encoding="utf-8")
    for name in ("checkpoints.db", "checkpoints.db-wal", "checkpoints.db-shm"):
        (tmp_path / name).write_bytes(b"every other thread's transcript")

    assert {path.name for path in agent_session.workspace_artifacts()} == {
        "shortlist.md",
        "follow-up.eml",
    }


def test_pending_actions_flattens_every_hanging_call() -> None:
    """One decision per pending call, or the middleware rejects the whole resume.

    A single interrupt can carry several `action_requests`, so counting
    interrupts is not counting actions — the CLI shipped exactly that bug,
    building one decision no matter how many calls were pending.
    """
    from ui.agent_session import pending_actions

    def _interrupt(count: int) -> Any:
        requests = [{"name": "save_draft", "args": {"filename": f"d{i}"}} for i in range(count)]
        return type("_Interrupt", (), {"value": {"action_requests": requests}})()

    state = type("_State", (), {"interrupts": [_interrupt(2), _interrupt(1)]})()
    agent = type("_Agent", (), {"get_state": lambda _self, _config: state})()

    assert len(pending_actions(agent, {})) == 3


def test_the_chat_page_refuses_a_thread_paused_without_the_gate() -> None:
    """The same fail-closed rule as `main`, in the second entry point.

    `interrupt_on` is only wired when approval is switched on, so opening a
    paused thread with the toggle off would leave a pending `save_draft` with no
    middleware left to stop it. Asserted on the page rather than by running it,
    for the reason the CLI's version gives: reaching the branch needs a graph
    paused mid-tool-call, which needs a model call.

    On the syntax tree rather than the source text, because the text version of
    this test stopped testing anything. It bounded its search with
    `.split("history = stored_messages", 1)[0]`, and that line left the page when
    the two checkpoint reads were consolidated into `thread_snapshot` — a split
    on an absent separator returns the whole string, so the bound silently became
    "anywhere below the guard", where the approval form's own `st.stop()` lives.
    Deleting the fail-closed stop would have kept it green. The tree carries no
    bound to go stale: it asks the guard itself what is in its body.
    """
    tree = ast.parse((PROJECT_ROOT / "app_pages" / "chat.py").read_text(encoding="utf-8"))
    guard = _sole_guard(tree, "actions", "require_approval")

    # A *direct* child of the body: nested inside a further condition, the stop
    # is reachable rather than certain, which is the whole distinction here.
    assert any(ast.unparse(statement) == "st.stop()" for statement in guard.body), (
        "the no-toggle path must stop unconditionally rather than fall through to a turn"
    )

    # The page's counterpart to `main`'s ordering assertion: nothing may be sent
    # to the graph above the guard.
    sends = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "stream_turn"
    ]
    assert sends, "the page no longer sends a turn; this test is checking nothing"
    assert guard.lineno < min(call.lineno for call in sends), (
        "the pending-approval check must run before any turn is sent, not after"
    )


def test_the_approval_form_shows_live_arguments_not_stored_ones() -> None:
    """A reviewer has to see the call they are actually approving.

    Keyed widgets persist. `st.text_area(..., key=f"arg_{i}_{name}")` writes its
    first value into session_state and reuses it from then on, so a second
    interrupt at the same index re-displayed the *previous* call's arguments
    while the decision applied to the new one. Found in a live run: the form
    still showed a placeholder body after the specialist had already redrafted
    with real figures, so approving would have written text the reviewer never
    saw. The fix is to render arguments with a stateless element, which is what
    this asserts — no widget key may be derived from an argument name.
    """
    source = (PROJECT_ROOT / "app_pages" / "chat.py").read_text(encoding="utf-8")
    assert 'key=f"arg_' not in source, (
        "argument display must not be a keyed widget — it will show a stale payload"
    )
    assert "st.code(" in source, "arguments are rendered with a stateless element"


def test_the_approval_toggle_renders_before_anything_that_can_rerun() -> None:
    """A safety gate must not switch itself off.

    Streamlit drops a keyed widget's value on any run where the widget does not
    render, and both sidebar controls call `st.rerun()` from inside the sidebar
    block — which aborts the run before anything below them renders. With the
    toggle declared last it never rendered on those runs, so `setdefault`
    re-initialised it to False on the next one. Observed live: switch approval
    on, click "New conversation", and the requirement was silently off again,
    which would have let the next `save_draft` through unattended.

    Order is the fix; `persist_state="session"` is the second line of defence.

    On the tree because the text version could not see the difference between a
    rerun and a *mention* of one. It stripped whole-line comments and nothing
    else, so a docstring or trailing comment naming `st.rerun()` above the
    toggle turned it red with no behavioural change — which is exactly what the
    fragment's docstring did when it was added, and the workaround was a rule
    written in CLAUDE.md for humans to remember. `ast` does not see prose.
    """
    tree = ast.parse((PROJECT_ROOT / "app_pages" / "chat.py").read_text(encoding="utf-8"))

    def _keyword(call: ast.Call, name: str) -> Any:
        for keyword in call.keywords:
            if keyword.arg == name and isinstance(keyword.value, ast.Constant):
                return keyword.value.value
        return None

    toggles = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "st.toggle"
        and _keyword(node, "key") == "require_approval"
    ]
    assert len(toggles) == 1, f"expected one approval toggle, found {len(toggles)}"
    assert _keyword(toggles[0], "persist_state") == "session", (
        "keep the toggle's value across runs where it does not render"
    )

    reruns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "st.rerun"
    ]
    assert reruns, "the page no longer reruns; this test is checking nothing"
    assert toggles[0].lineno < min(node.lineno for node in reruns), (
        "the approval toggle must render before any st.rerun(), or its value is dropped"
    )


def test_the_decision_control_gets_a_fresh_key_for_each_approval_round() -> None:
    """The fail-closed default has to survive a second interrupt.

    `default="Reject"` only applies to a key Streamlit holds no value for; on
    every later render the *stored* value wins. Keyed by position alone,
    approving one call left the next interrupt's form already reading
    "Approve" — so a reviewer who scanned the new arguments and pressed submit
    approved something they never chose. Measured: `clear_on_submit=True` does
    not restore the default, and deleting the key mid-run breaks the widget.
    Advancing a round number on each submission mints keys never seen before,
    which does.
    """
    source = (PROJECT_ROOT / "app_pages" / "chat.py").read_text(encoding="utf-8")
    assert 'key=f"decision_{approval_round}_{index}"' in source, (
        "key the decision control per approval round, not by position alone"
    )
    assert "st.session_state.approval_round += 1" in source, (
        "the round must advance on submit, or the next form reuses these keys"
    )


def test_the_app_and_the_cli_flatten_interrupts_identically() -> None:
    """Two implementations of one contract, over the same checkpoint database.

    `HumanInTheLoopMiddleware` validates that the number of decisions equals the
    number of hanging tool calls, so a divergence here does not degrade — the
    whole resume raises. The CLI shipped this wrong once, building one decision
    no matter how many calls were pending, which is why the pair is pinned
    rather than either side alone. The empty-requests case is included because
    that is where the placeholder fallback lives.
    """
    import main
    from ui.agent_session import pending_actions

    def _interrupt(count: int) -> Any:
        requests = [{"name": "save_draft", "args": {"filename": f"d{i}"}} for i in range(count)]
        return type("_Interrupt", (), {"value": {"action_requests": requests}})()

    interrupts = [_interrupt(2), _interrupt(1), _interrupt(0)]
    state = type("_State", (), {"interrupts": interrupts})()
    agent = type("_Agent", (), {"get_state": lambda _self, _config: state})()

    assert pending_actions(agent, {}) == main._action_requests(interrupts)


def test_the_workspace_browser_is_a_fragment() -> None:
    """Browsing an artifact must not re-read the checkpoint.

    The file selectbox and the preview expander are pure viewers — nothing they
    do changes what the conversation is. But a widget outside a fragment reruns
    the *whole script*, and this script's dominant cost is `thread_snapshot`,
    which takes the checkpointer's lock and deserialises every message in the
    thread before re-rendering the transcript. Paying that to answer "what is in
    this file" gets worse with every turn, which is exactly the shape of cost
    that never looks broken.

    Asserted on the page rather than by running it, because the alternative is a
    live Streamlit runtime. The placement half matters too: the fragment writes
    into `st.sidebar`, and Streamlit only lets it redraw there if that container
    was already written to during the full run.

    On the tree, because `"@st.fragment" in source` would have passed with the
    decorator on any function in the file, or in a comment.
    """
    tree = ast.parse((PROJECT_ROOT / "app_pages" / "chat.py").read_text(encoding="utf-8"))

    browser = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_workspace_browser"
        ),
        None,
    )
    assert browser is not None, "chat.py has no top-level `def _workspace_browser`"
    assert any(ast.unparse(node) == "st.fragment" for node in browser.decorator_list), (
        "_workspace_browser must carry @st.fragment, or every file click re-reads "
        "the whole checkpoint"
    )

    sidebars = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(ast.unparse(item.context_expr) == "st.sidebar" for item in node.items)
    ]
    assert len(sidebars) == 1, f"expected one `with st.sidebar:` block, found {len(sidebars)}"

    calls = [
        node
        for node in ast.walk(sidebars[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_workspace_browser"
    ]
    assert calls, "the fragment must be called inside the `with st.sidebar:` block"
    assert calls[0].lineno > sidebars[0].body[0].lineno, (
        "the sidebar must receive a write before the fragment renders into it"
    )


# The next three pin one Streamlit rule, a layer under the widget-state family
# above: **a collapsed expander still renders its body**, and **a stateful
# expander's identity is its parameters**. Both halves were measured on 1.60 --
# two same-label stateful expanders in one run raise StreamlitDuplicateElementId
# and the page renders nothing; a shared constant key raises
# StreamlitDuplicateElementKey instead. So the danger runs both ways: too little
# keying kills the page, and no guard silently restores the payload.


def test_the_lazy_expander_helper_sets_the_flag_that_makes_open_meaningful() -> None:
    """`.open` is only a boolean under `on_change="rerun"`.

    The rule lives in one helper now, so this is the one place it can go
    missing. Under the default `"ignore"` every `.open` is `None`, which makes
    each caller's guard permanently false and stops tool results rendering
    at all — the opposite failure from the one the guard exists to prevent, and
    the reason both are pinned rather than just the guard.
    """
    helper = _function_named(PROJECT_ROOT / "ui" / "elements.py", "lazy_expander")

    call = _sole_call(helper, "st.expander")
    assert _keyword_constant(call, "on_change") == "rerun", (
        'lazy_expander must pass on_change="rerun"; without it `.open` is None at '
        "every call site and every guarded body goes dark"
    )
    assert any(argument.arg == "key" for argument in helper.args.kwonlyargs), (
        "`key` must stay keyword-only and required — an unkeyed stateful expander "
        "raises StreamlitDuplicateElementId the moment two labels coincide"
    )


@pytest.mark.parametrize(
    ("module", "function", "variable", "identity"),
    [
        ("ui/agent_session.py", "render_message", "panel", "message"),
        ("app_pages/chat.py", "_workspace_browser", "preview", "chosen"),
    ],
)
def test_expander_bodies_render_only_when_open(
    module: str, function: str, variable: str, identity: str
) -> None:
    """Each lazy site guards its body, and keys on what it is showing.

    Three ways to regress, and the tree catches each. Rendering the body outside
    the `if x.open:` guard puts the payload back on every rerun -- the original
    defect, and invisible. Rendering it outside `with x:` leaves it on the page
    as a loose block below its own expander. And a key that does not derive from
    the thing on display is the widget-state defect this repo has shipped four
    times: a *constant* key here is not a milder version of no key, it is fatal
    on the second element.
    """
    scope = _function_named(PROJECT_ROOT / module, function)

    call = _sole_call(scope, "lazy_expander")
    key = next((keyword.value for keyword in call.keywords if keyword.arg == "key"), None)
    assert key is not None, f"{function} must key its expander"
    assert not isinstance(key, ast.Constant), (
        "a constant key is worse than no key: the second element raises "
        "StreamlitDuplicateElementKey and the page renders nothing"
    )
    assert identity in {
        node.id for node in ast.walk(key) if isinstance(node, ast.Name)
    }, (
        f"the key must derive from `{identity}`, or one item's open state applies "
        "to the next one shown"
    )

    guard = _sole_guard(scope, variable, "open")
    body = _sole_call(scope, "st.code")
    assert any(body is node for statement in guard.body for node in ast.walk(statement)), (
        "the body must render inside the open guard, or it ships on every rerun "
        "exactly as it did before"
    )
    withs = [
        node
        for node in ast.walk(guard)
        if isinstance(node, ast.With)
        and any(ast.unparse(item.context_expr) == variable for item in node.items)
    ]
    assert any(body is node for block in withs for node in ast.walk(block)), (
        f"the body must render inside `with {variable}:`, or it lands on the page "
        "as a loose block underneath its own expander rather than inside it"
    )


def test_every_market_data_function_is_cached() -> None:
    """`ui/market_data.py` exists to be the dashboard's cached reads.

    On the mock each of these is a free in-memory scan, which is exactly why an
    uncached one survives review: nothing is slow and nothing is wrong. But
    `get_provider` is the seam the README promises you can point at a real feed
    in one edit, and on that day an uncached reader is a network round-trip on
    every slider drag.

    The rule is every module-level function rather than "functions that call
    `get_provider`", because that earlier shape keyed on two hardcoded callee
    names: a reader reaching the provider through any new helper was simply not
    collected, and passed by not being looked at. The `ttl`/`max_entries` half
    is checked rather than only promised in the message.
    """
    tree = ast.parse((PROJECT_ROOT / "ui" / "market_data.py").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert functions, "market_data.py has no module-level functions; this checks nothing"

    uncached, unbounded = [], []
    for node in functions:
        decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
        data = [text for text in decorators if text.startswith("st.cache_data")]
        if not data and not any(text.startswith("st.cache_resource") for text in decorators):
            uncached.append(node.name)
        elif data and not any(("ttl=" in text or "max_entries=" in text) for text in data):
            unbounded.append(node.name)

    assert not uncached, (
        f"these run on every rerun with no cache: {uncached}. Add st.cache_data "
        "(with a ttl or max_entries) or st.cache_resource."
    )
    assert not unbounded, (
        f"these cache without a ttl or max_entries and grow forever: {unbounded}"
    )


def _market_page_constant(name: str) -> Any:
    """Read one literal constant out of `market.py` without running the page."""
    source = (PROJECT_ROOT / "app_pages" / "market.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a module-level constant in market.py")


def test_the_listings_table_drops_columns_rather_than_masking_them() -> None:
    """`column_config={name: None}` hides a column; it does not withhold it.

    The values are still serialised into the page payload, so a `description`
    nobody can see still ships on every row of every filter combination. Here
    that is payload rather than disclosure — the mock has no secrets — but the
    same two lines over a real feed are the difference, and the fix costs one
    `.drop`.

    The second half guards the fix itself: `drop(columns=...)` raises `KeyError`
    on a name that is not there, so renaming a `Listing` field would take the
    page down rather than degrade. Checked against a real frame.
    """
    from ui.market_data import listings_frame

    dropped = _market_page_constant("_NOT_IN_THE_TABLE")
    source = (PROJECT_ROOT / "app_pages" / "market.py").read_text(encoding="utf-8")
    assert "frame.drop(columns=_NOT_IN_THE_TABLE)" in source, (
        "the table must be given a frame without these columns, not a config that hides them"
    )
    for name in dropped:
        assert f'"{name}": None' not in source, (
            f"{name} is dropped and also masked in column_config — one of the two is stale"
        )

    frame = listings_frame("Hilo", "HI", None, "active")
    assert not frame.empty, "fixture market went empty; the column check below proves nothing"
    missing = [name for name in dropped if name not in frame.columns]
    assert not missing, f"_NOT_IN_THE_TABLE names columns that do not exist: {missing}"


def _theme_config() -> dict[str, Any]:
    return tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))


def _dotted_keys(section: dict[str, Any], prefix: str) -> list[str]:
    """Every leaf setting in a config section, as the dotted key Streamlit uses."""
    keys: list[str] = []
    for name, value in section.items():
        if isinstance(value, dict):
            keys.extend(_dotted_keys(value, f"{prefix}.{name}"))
        else:
            keys.append(f"{prefix}.{name}")
    return keys


def test_every_theme_setting_is_a_real_config_option() -> None:
    """Streamlit *discards* a misplaced theme key; it does not reject the file.

    An option registered at `[theme]` only — `chartCategoricalColors` is one —
    logs "not a valid config option" to stderr when it appears under
    `[theme.light]`, and is then simply absent. The app starts, looks styled,
    and silently uses the built-in palette. That shipped here: both modes
    carried a hand-tuned categorical palette that Streamlit never read, and the
    test guarding this file checked two keys out of sixty, so the suite was
    green over a theme discarding a third of its content.

    `st.get_option` is the registry, so ask it about every leaf rather than
    maintaining a second list of what is valid.
    """
    import streamlit as st

    unknown = []
    for key in _dotted_keys(_theme_config()["theme"], "theme"):
        try:
            st.get_option(key)
        except RuntimeError:
            unknown.append(key)

    assert not unknown, (
        "these theme settings are not config options and are silently discarded — "
        f"check whether they are registered at [theme] only: {unknown}"
    )


def test_the_theme_leaves_the_light_dark_switch_working() -> None:
    """A custom theme with only `[theme]` locks the app to a single mode.

    Streamlit shows the light/dark control in the settings menu only when both
    `[theme.light]` and `[theme.dark]` are defined — every bundled theme
    template ships one `[theme]` block and therefore takes that switch away
    without saying so. This app is used at a desk and in daylight, so both
    halves are authored. Nothing raises if one is deleted; the toggle just
    disappears.
    """
    theme = _theme_config()["theme"]
    for mode in ("light", "dark"):
        assert mode in theme, f"[theme.{mode}] is missing — the mode switch will not render"
        assert theme[mode].get("primaryColor"), f"[theme.{mode}] defines no primaryColor"
    assert theme["light"]["backgroundColor"] != theme["dark"]["backgroundColor"], (
        "the two modes must actually differ"
    )


# --- toolchain configuration ----------------------------------------------
#
# "N tests, ty clean, ruff clean" is only a gate if "clean" cannot quietly
# change meaning. These read the live pyproject and the live docs rather than a
# copy, for the same reason `_rules()` imports WORKSPACE_PERMISSIONS: a
# hand-mirrored duplicate cannot fail when someone edits the original.


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _documented_pins(tool: str) -> set[str]:
    """Every `uvx <tool>@<version>` pin written in the docs."""
    found: set[str] = set()
    for name in ("CLAUDE.md", "README.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        found.update(re.findall(rf"uvx {tool}@([0-9]+(?:\.[0-9]+)*)", text))
    return found


def test_ruff_pin_is_enforced_not_merely_documented() -> None:
    """A version inside a Markdown fence enforces nothing.

    Without `required-version`, a bare `uvx ruff check .` resolves whatever is
    newest, applies a different rule set, and still prints "All checks passed!".
    """
    required = _pyproject()["tool"]["ruff"]["required-version"]
    assert required.startswith("=="), "pin an exact version, not a range"
    assert _documented_pins("ruff") == {required.removeprefix("==")}, (
        "the documented `uvx ruff@...` invocations and required-version disagree"
    )


def test_ty_pin_is_consistent_across_docs() -> None:
    """ty has no `required-version`, so its pin is a convention nothing enforces.

    The strongest check available is that the docs do not contradict each other.
    If ty ever grows the setting, tighten this the way the ruff test is written.
    """
    assert len(_documented_pins("ty")) == 1


def test_ruff_extends_the_defaults_rather_than_replacing_them() -> None:
    """`select` silently drops every default rule not re-listed.

    An earlier config did exactly that and gave up ~300 rules this codebase
    already passed, B006, BLE001 and DTZ005 among them. `required-version` is
    what fixes which defaults apply, so `select` has nothing left to buy.
    """
    lint = _pyproject()["tool"]["ruff"]["lint"]
    assert "select" not in lint, "use extend-select; select discards the defaults"
    assert {"I", "UP", "PLR0402"} <= set(lint["extend-select"])


def _check_script_pin(tool: str) -> str:
    """The version `scripts/check.sh` actually runs for ``tool``."""
    text = (PROJECT_ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    match = re.search(rf"^{tool.upper()}_VERSION=([0-9.]+)$", text, re.MULTILINE)
    assert match, f"scripts/check.sh does not define {tool.upper()}_VERSION"
    return match.group(1)


def test_check_script_pins_match_the_docs() -> None:
    """`scripts/check.sh` is where the pins are enforced rather than described.

    A version inside a Markdown fence binds whoever reads the fence. The script
    binds whoever runs the script — a human shell, the Stop hook, or any future
    CI — which is the same reason `required-version` beats documenting the ruff
    pin. This test is what stops the two drifting apart.
    """
    assert _check_script_pin("ruff") == _pyproject()["tool"]["ruff"][
        "required-version"
    ].removeprefix("==")
    for tool in ("ruff", "ty"):
        assert _documented_pins(tool) == {_check_script_pin(tool)}, (
            f"scripts/check.sh runs a different {tool} than the docs document"
        )


_RUN_STEP = re.compile(r"^\s*run:\s*(.+?)\s*$", re.MULTILINE)


def test_ci_calls_the_check_script_rather_than_restating_it() -> None:
    """CI must not become a fourth place the pins live.

    The workflow's whole argument is that `scripts/check.sh` decides what "done"
    means, so `test_check_script_pins_match_the_docs` guards one file instead of
    several. Inlining `uvx ruff check .` and `uv run pytest` as YAML steps while
    debugging a red build is the obvious shortcut, and it drops both @0.16.1 and
    --floor with every other test still green.

    Checks only `run:` lines, because the rationale for calling the script is
    written in the comments and would otherwise match.
    """
    workflow = PROJECT_ROOT / ".github" / "workflows" / "check.yml"
    commands = _RUN_STEP.findall(workflow.read_text(encoding="utf-8"))

    assert "scripts/check.sh --floor" in commands, (
        "CI must run the script, with the floor leg -- that is the whole point"
    )
    assert "uv sync --locked" in commands, (
        "--locked is the only check that uv.lock is current with pyproject.toml"
    )
    for command in commands:
        for inlined in ("uvx", "pytest", "ruff", "ty@"):
            assert inlined not in command, (
                f"{inlined!r} is inlined in CI ({command!r}); call scripts/check.sh instead"
            )


def test_ty_fails_the_build_on_warnings() -> None:
    """Adopted at zero warnings — the only cheap moment. Dropping it lets them
    accumulate with nothing reporting that the gate got weaker."""
    assert _pyproject()["tool"]["ty"]["terminal"]["error-on-warning"] is True


def _dotenv_calls(tree: ast.AST) -> list[ast.Call]:
    """Every `load_dotenv(...)` call in ``tree``, at any depth."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_dotenv"
    ]


def _package_imports(tree: ast.AST) -> list[ast.ImportFrom]:
    """Every `from real_estate_agent... import ...`, at any depth."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("real_estate_agent")
    ]


def test_config_does_not_load_dotenv_at_import() -> None:
    """A library module reading `.env` at import applies it to every consumer.

    `config.py` used to call `load_dotenv()` at module scope, so importing the
    package — which this suite does on every run — silently adopted a
    developer's personal configuration. `LANGSMITH_TRACING` was the expensive
    one: 17 billable root runs per run, on a suite documented as offline.
    `REA_MODEL` and `REA_PROJECT_ROOT` are the quiet ones, and worse in kind.
    The first hands `build_agent()` a different model, and CLAUDE.md's own note
    says the middleware stack resolves from a per-`provider:model` profile, so
    `test_agent_exposes_planning_and_delegation` would assert against a graph CI
    never builds. The second repoints `PROJECT_ROOT`, which anchors
    `_pyproject`, `_documented_pins`, `_check_script_pin` and the documented
    count — the whole toolchain cluster would read another checkout's files and
    still pass or fail for reasons no one could see from here.

    Reads the tree rather than the imported module: `config.load_dotenv` would
    also be absent if the name were merely rebound, and the question is whether
    the *call* is in the source at all.
    """
    tree = ast.parse((PROJECT_ROOT / "src" / "real_estate_agent" / "config.py").read_text("utf-8"))
    assert not _dotenv_calls(tree), (
        "config.py calls load_dotenv() again. Importing the package then applies "
        "a developer's .env to every consumer, the test suite included. Load it "
        "in main.py and streamlit_app.py instead."
    )


def test_each_entry_point_loads_dotenv_before_the_package() -> None:
    """Loading `.env` after the import is too late, and looks identical.

    `PROJECT_ROOT`, `DEFAULT_MODEL` and `SUBAGENT_MODEL` are evaluated when
    `config.py` is imported, not when they are read. So moving `load_dotenv()`
    out of that module only works if each entry point calls it *before* the
    import — a `load_dotenv()` at the top of `main()` underneath a module-scope
    `from real_estate_agent import build_agent` would run second and `REA_MODEL`
    in `.env` would be ignored, with nothing to show for it but the default
    model in the sidebar caption.

    `main.py` therefore keeps both the call and its package imports inside
    `main()`; this asserts that pairing rather than the mere presence of the
    call. `streamlit_app.py` needs no such care because it imports no page
    module until `page.run()` — so what is checked there is that it has not
    since grown a package import above the call.
    """
    main_tree = ast.parse((PROJECT_ROOT / "main.py").read_text("utf-8"))
    calls, imports = _dotenv_calls(main_tree), _package_imports(main_tree)
    assert calls, "main.py must load .env; config.py no longer does it"
    assert imports, "main.py imports the package somewhere, or this test is checking nothing"
    assert not [node for node in main_tree.body if isinstance(node, ast.ImportFrom) and node in imports], (
        "a module-scope real_estate_agent import in main.py runs before any "
        "load_dotenv() this file can call, so REA_* in .env would be ignored"
    )
    assert min(call.lineno for call in calls) < min(node.lineno for node in imports), (
        "main.py imports real_estate_agent before loading .env"
    )

    app_tree = ast.parse((PROJECT_ROOT / "streamlit_app.py").read_text("utf-8"))
    app_calls = _dotenv_calls(app_tree)
    assert app_calls, "streamlit_app.py must load .env before page.run() imports a page"
    assert not _package_imports(app_tree), (
        "streamlit_app.py now imports real_estate_agent directly; either move the "
        "import below load_dotenv() or assert the ordering here as main.py does"
    )


def test_the_suite_does_not_trace_to_langsmith() -> None:
    """Tracing on during the suite spends a LangSmith quota and nothing reports it.

    `config.py` calls `load_dotenv()` at import, so importing the package pulls a
    developer's real `.env` — which `.env.example` documents as carrying
    `LANGSMITH_TRACING=true` — into the environment. Every `tool.invoke()` in
    this file is then a **root** run, and LangSmith bills per trace rather than
    per span, so 17 one-span traces per run cost what 17 whole agent
    conversations would. The Stop hook runs this suite every turn.

    This is the silent-failure shape the invariants section exists for: the
    suite stays green, `pytest -q` still prints no errors, and the only symptom
    is an exhausted free tier weeks later.

    The first assertion is the load-bearing one, and asserting `tracing_is_enabled()`
    alone was **not enough** — that was this test's first version and it was
    vacuous everywhere it mattered. The predicate only reads the environment, so
    on a checkout with no `.env` — CI, or any contributor who never copied
    `.env.example` — nothing sets the variable, tracing is already off, and the
    test passes whether or not `tests/conftest.py` still exists. Measured against
    a `git archive` of this commit: deleting the conftest left the run green.
    A guard that only fires on the one machine that already has the problem is
    not a guard.

    Asserting the *value* conftest writes fixes that in both directions. Absent
    the conftest the name is unset (`None`) on a clean checkout and `"true"` on a
    developer's, and neither equals `"false"`; with `load_dotenv(override=True)`
    in `config.py` it is `"true"`. `tracing_is_enabled()` stays as the second
    assertion because it is what LangChain actually consults, so it still catches
    a langsmith release that reads some name conftest does not set.

    Deletion is the only case this test owns alone. `pytest_collection_finish` in
    the conftest covers the rest earlier — before the first `invoke` rather than
    after the last, and on filtered runs that never collect this test.
    """
    assert os.environ.get("LANGSMITH_TRACING") == "false", (
        "tests/conftest.py did not run, or no longer sets LANGSMITH_TRACING. Every "
        "tool.invoke() in this suite is a billable LangSmith root run without it, "
        "and on a checkout with no .env nothing else here would notice."
    )
    assert not tracing_is_enabled(), (
        "LangSmith tracing is enabled during the test suite -- every tool.invoke() "
        "here is a billable root run. tests/conftest.py must disable it before "
        "real_estate_agent.config calls load_dotenv()."
    )


# Every option that collects a subset. A subset says nothing about the whole
# suite's size, and CLAUDE.md documents `-k "traversal"` as a normal invocation.
# `lf`/`failedfirst` matter most: this test counting itself means a filtered run
# fails it, which puts it in the last-failed set, which makes the next `--lf`
# collect only it — a red `--lf` that no code change can clear.
_SUBSETTING_OPTIONS = ("keyword", "markexpr", "deselect", "lf", "failedfirst", "stepwise")


def test_documented_test_count_matches_the_suite(request: pytest.FixtureRequest) -> None:
    """The suite size is written into CLAUDE.md and README.md and drifts silently.

    This one counts itself, which is the point: adding a test is exactly the
    moment the documented number goes stale, so the check has to fire then.

    It is enforced by full-suite runs and by nothing else — see the skip guard.
    """
    option = request.config.option
    filtered = [name for name in _SUBSETTING_OPTIONS if getattr(option, name, None)]
    if filtered:
        pytest.skip(f"filtered run ({', '.join(filtered)}); the count needs the whole suite")
    if any("::" in argument for argument in request.config.args):
        pytest.skip("explicit node id collects a subset")

    total = request.session.testscollected
    for name in ("CLAUDE.md", "README.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        # Anchored: an unanchored "43 tests" also matches "143 tests".
        assert re.search(rf"\b{total} tests\b", text), (
            f"{name} does not say {total} tests, which is what this run collected. "
            "If this was a filtered run, the guard above missed a subsetting option."
        )

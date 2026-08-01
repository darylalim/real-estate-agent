"""Tests that pin the behaviour easy to break by accident.

None of these call the Anthropic API. The agent test builds the graph with a
dummy key to assert on wiring only — the things that fail silently at runtime
(a missing planning tool, a permission rule in the wrong order) rather than
loudly at construction.
"""

from __future__ import annotations

import email
import email.policy
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from deepagents import FilesystemPermission
from deepagents.middleware.filesystem import _check_fs_permission
from langchain_core.tools import BaseTool

from real_estate_agent.config import PROJECT_ROOT
from real_estate_agent.providers import MockListingsProvider
from real_estate_agent.tools import comms, documents
from real_estate_agent.tools.comms import make_comms_tools
from real_estate_agent.tools.documents import make_document_tools
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


def test_search_respects_filters(provider: MockListingsProvider) -> None:
    results = provider.search(
        city="Round Rock", max_price=600_000, min_beds=3, status="active"
    )
    assert results
    for listing in results:
        assert listing.city == "Round Rock"
        assert listing.price <= 600_000
        assert listing.beds >= 3
        assert listing.status == "active"


def test_expensive_market_can_return_nothing(provider: MockListingsProvider) -> None:
    """The two markets are priced apart on purpose.

    Austin 78704/78745 runs well above Round Rock, so a modest budget clears no
    Austin inventory. That asymmetry is what makes `qualify_lead`'s feasibility
    check meaningful — an empty result is a real answer, not a bug.
    """
    assert provider.search(city="Austin", max_price=450_000, min_beds=3) == []
    assert provider.search(city="Round Rock", max_price=450_000, min_beds=3)


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
    footage was spread too widely across a 66-listing dataset.
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

    assert set(rejected) == {"not_sold", "stale", "outside_radius", "size_mismatch"}
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
        "outside_radius",
        "size_mismatch",
    }
    assert payload["search"]["max_sqft_delta_pct"] == 0.30


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
    assert len(provider.search(city="Austin", status="Active")) == len(
        provider.search(city="Austin", status="active")
    )
    assert len(provider.search(city="AUSTIN", property_type="Condo")) == len(
        provider.search(city="austin", property_type="condo")
    )


def test_market_statistics_window_filters_the_numerator(
    provider: MockListingsProvider,
) -> None:
    """Regression: months_back divided but never filtered, so the same 16 sales
    produced months-of-inventory of 5.2 or 21.0 purely by changing the window."""
    tools = {tool.name: tool for tool in make_market_tools(provider)}
    short = json.loads(
        tools["market_statistics"].invoke({"city": "Austin", "months_back": 3})
    )
    long = json.loads(
        tools["market_statistics"].invoke({"city": "Austin", "months_back": 12})
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


def test_qualify_lead_does_not_blame_budget_for_a_bedroom_shortfall(
    provider: MockListingsProvider,
) -> None:
    """Regression: a $2M budget in a $683k market was reported as clearing 8%."""
    tools = {tool.name: tool for tool in make_comms_tools(provider)}
    payload = json.loads(
        tools["qualify_lead"].invoke(
            {
                "name": "Jane",
                "target_city": "Round Rock",
                "budget_max": 2_000_000,
                "timeline_months": 2,
                "pre_approved": True,
                "min_beds": 5,
            }
        )
    )
    feasibility = payload["market_feasibility"]
    # Budget clears everything that meets the requirements; beds are binding.
    assert feasibility["share_of_qualifying_inventory_affordable"] == 1.0
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
                "subject": "Round Rock shortlist",
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
    assert "Subject: Round Rock shortlist" in raw
    # Makes a mail client open this as an editable draft, not a received message.
    assert "X-Unsent: 1" in raw
    # Absence of From/Date is deliberate — it keeps the message clearly unsent.
    assert "\nFrom:" not in raw

    assert payload["mailto_url"].startswith("mailto:jane%40example.com?")
    assert "Round%20Rock%20shortlist" in payload["mailto_url"]


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


def test_ty_fails_the_build_on_warnings() -> None:
    """Adopted at zero warnings — the only cheap moment. Dropping it lets them
    accumulate with nothing reporting that the gate got weaker."""
    assert _pyproject()["tool"]["ty"]["terminal"]["error-on-warning"] is True


def test_documented_test_count_matches_the_suite(request: pytest.FixtureRequest) -> None:
    """The suite size is written into CLAUDE.md and README.md and drifts silently.

    This one counts itself, which is the point: adding a test is exactly the
    moment the documented number goes stale, so the check has to run then.

    Skipped on a filtered run. `-k`, `-m`, and an explicit node id all collect a
    subset — CLAUDE.md documents `-k "traversal"` as a normal invocation — and a
    subset says nothing about the whole suite's size.
    """
    option = request.config.option
    if option.keyword or option.markexpr:
        pytest.skip("filtered with -k/-m; the count is only meaningful for the whole suite")
    if any("::" in argument for argument in request.config.args):
        pytest.skip("explicit node id collects a subset")

    total = request.session.testscollected
    for name in ("CLAUDE.md", "README.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert f"{total} tests" in text, (
            f"{name} does not say {total} tests, which is what the suite now collects"
        )

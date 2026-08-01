"""Tests that pin the behaviour easy to break by accident.

None of these call the Anthropic API. The agent test builds the graph with a
dummy key to assert on wiring only — the things that fail silently at runtime
(a missing planning tool, a permission rule in the wrong order) rather than
loudly at construction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

import pytest

from deepagents import FilesystemPermission
from deepagents.middleware.filesystem import _check_fs_permission

from real_estate_agent.providers import MockListingsProvider
from real_estate_agent.tools.documents import make_document_tools
from real_estate_agent.tools.market import make_market_tools


@pytest.fixture
def provider() -> MockListingsProvider:
    return MockListingsProvider()


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

    assert set(rejected) == {"not_sold_or_stale", "outside_radius", "size_mismatch"}
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
        "not_sold_or_stale",
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
    import real_estate_agent.tools.documents as documents

    monkeypatch.setattr(documents, "DOCUMENTS_DIR", tmp_path)
    (tmp_path / "lease.md").write_text("Rent is $2,400 per month.", encoding="utf-8")

    tools = {tool.name: tool for tool in documents.make_document_tools()}
    payload = json.loads(tools["extract_document_text"].invoke({"filename": "lease.md"}))
    assert "2,400" in payload["text"]


# --- draft handoff --------------------------------------------------------


def test_save_draft_emits_md_eml_and_mailto(tmp_path, monkeypatch) -> None:
    import real_estate_agent.tools.comms as comms

    monkeypatch.setattr(comms, "DRAFTS_DIR", tmp_path)
    tools = {tool.name: tool for tool in comms.make_comms_tools(MockListingsProvider())}

    payload = json.loads(
        tools["save_draft"].invoke(
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


def test_save_draft_omits_mailto_when_body_too_long(tmp_path, monkeypatch) -> None:
    """mailto: silently truncates in real clients, so degrade explicitly."""
    import real_estate_agent.tools.comms as comms

    monkeypatch.setattr(comms, "DRAFTS_DIR", tmp_path)
    tools = {tool.name: tool for tool in comms.make_comms_tools(MockListingsProvider())}

    payload = json.loads(
        tools["save_draft"].invoke(
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


def test_save_draft_sanitises_filename(tmp_path, monkeypatch) -> None:
    import real_estate_agent.tools.comms as comms

    monkeypatch.setattr(comms, "DRAFTS_DIR", tmp_path)
    tools = {tool.name: tool for tool in comms.make_comms_tools(MockListingsProvider())}

    payload = json.loads(
        tools["save_draft"].invoke(
            {"filename": "../../escape/attempt", "subject": "s", "body": "b"}
        )
    )
    assert Path(payload["markdown_path"]).parent == tmp_path


# --- permission containment ----------------------------------------------


def _rules() -> list[FilesystemPermission]:
    """Mirrors build_agent's rules. First match wins, so order is load-bearing."""
    return [
        FilesystemPermission(
            operations=["read", "write"], paths=["/workspace/**"], mode="allow"
        ),
        FilesystemPermission(operations=["read"], paths=["/skills/**"], mode="allow"),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/.env", "/.env.*", "/.venv/**", "/.git/**"],
            mode="deny",
        ),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]


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

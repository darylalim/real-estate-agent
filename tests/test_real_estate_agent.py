"""Tests that pin the behaviour easy to break by accident.

None of these call the Anthropic API. The agent test builds the graph with a
dummy key to assert on wiring only — the things that fail silently at runtime
(a missing planning tool, a permission rule in the wrong order) rather than
loudly at construction.
"""

from __future__ import annotations

import json
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
    subject = provider.search(city="Austin", status="active", limit=1)[0]
    comps = provider.comparables(subject.listing_id, radius_miles=1.5, months_back=6)
    assert comps, "expected comps at the documented default radius"
    for comp in comps:
        assert comp.status == "sold"
        assert comp.sold_price is not None
        assert comp.listing_id != subject.listing_id


# --- market tools ---------------------------------------------------------


def test_find_comparables_reports_value_range(provider: MockListingsProvider) -> None:
    tools = {tool.name: tool for tool in make_market_tools(provider)}
    subject = provider.search(city="Austin", status="active", limit=1)[0]

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

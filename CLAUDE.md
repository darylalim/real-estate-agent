# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A real estate agent built on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) 0.7.1: an
orchestrator that holds no domain tools and delegates to four specialists over a shared `/workspace/` filesystem.

`README.md` is current and detailed — it covers the safety rationale, the live-run verification table, and the
defect history. Read it before changing containment rules, the draft handoff, or the mock dataset. This file
covers what the README doesn't: commands, the wiring that spans several files, and the invariants that fail
silently.

## Commands

```bash
uv sync                                          # install (Python pinned to 3.14 by .python-version)
cp .env.example .env                             # then add ANTHROPIC_API_KEY

uv run python main.py "Find 3-bed homes in Round Rock under $600k"
uv run python main.py                            # interactive
uv run python main.py --require-approval         # pause before save_draft
uv run python main.py --thread <id>              # continue a conversation

uv run pytest tests/ -q                          # full suite: 38 tests, ~0.7s, no API calls
uv run pytest tests/test_real_estate_agent.py::test_permission_matrix -q   # one test
uv run pytest -q -k "traversal"                  # by keyword

uvx ty@0.0.65 check                              # type check — pinned; not a declared dependency
```

**Definition of done in this repo is "N tests, ty clean"** — every commit message so far ends with it. Both
halves of that phrase are pinned so they mean the same thing on any machine: `.python-version` fixes the
interpreter at 3.14 (`requires-python` alone only sets a floor, so a fresh `uv sync` elsewhere would pick
whatever newest interpreter it found), and the `ty` version is pinned **in the command** rather than in
`pyproject.toml` — `uvx` runs it in its own ephemeral environment, so ty's dependencies never enter `uv.lock`,
but that also hides the version. ty is pre-1.0 and its diagnostics move fast; unpinned, "ty clean" can flip
between two runs on identical source. Bump the pin deliberately, don't drop it. `pytest` is the only dev
dependency, and `.gitignore` already ignores `.ruff_cache/` and `.ty_cache/`. No formatter or linter is
configured.

The whole suite runs offline. `test_agent_exposes_planning_and_delegation` builds the real graph under a dummy
key to assert on wiring only, so there is no reason to skip tests for cost or network.

## Architecture

### The injection spine

One seam runs the length of the app, and every layer below it is parameterised rather than hardcoded:

```
ListingsProvider (Protocol)  →  make_*_tools(provider)  →  build_subagents(...tools)  →  build_agent()
```

- `providers/base.py` defines `Listing` and the `ListingsProvider` **Protocol** (structural — a real MLS adapter
  subclasses nothing). `DiagnosticListingsProvider` is an *optional* extension; `find_comparables`
  feature-detects it with `getattr(provider, "comparables_with_diagnostics", None)`.
- Tools are **factories closing over the provider**, not module-level `@tool` functions. That closure is the
  only reason the data source is injectable.
- Taking the agent live means implementing `search` / `get` / `comparables` and passing
  `build_agent(provider=...)`. No tool, subagent, or prompt changes.
- `search_listings` (the tool) deliberately does not expose `sold_within_months`; only `market_statistics` uses
  it. A real provider must still implement it — the months-of-inventory maths is wrong without it.

### Two path universes

This is the easiest thing in the repo to get wrong, because both look like absolute paths.

| | Used by | Rooted at | Example |
|---|---|---|---|
| **Virtual** | `read_file` / `write_file` / `ls`, and subagent `skills` entries | `PROJECT_ROOT`, via `FilesystemBackend(root_dir=..., virtual_mode=True)` | `/workspace/shortlist.md`, `/skills/cma-analysis` |
| **Real** | `tools/documents.py`, `tools/comms.py` (plain `pathlib` from `config.py`) | the actual filesystem | `/Users/…/workspace/drafts/x.md` |

Consequences worth remembering:

- `extract_document_text(filename=...)` takes a path **relative to the documents dir** (`lease.pdf`). Passing the
  virtual `/workspace/documents/lease.pdf` is *rejected*: `Path.__truediv__` discards the left operand when the
  right is absolute, so it resolves outside the root and trips the containment check.
- `save_draft` returns real absolute paths, which the agent's own `read_file` cannot open. That is fine — those
  paths are for the human, and the liaison prompt says to surface them.

### Skills

Three specialists load a skill; `property-search` is prompt-only on purpose. **Skills are not inherited from the
orchestrator** — each subagent declares its own in `subagents.py`, pinned by `test_subagents_carry_their_own_skills`.
The criterion is size: CMA adjustment grids and clause checklists are too big for a system prompt; a
property-search workflow is not.

## Invariants that fail silently

Each of these is load-bearing and has a test. Breaking one produces plausible-looking wrong output, not an error.

- **Permission rules are order-sensitive: first match wins, and an unmatched path defaults to *allow*.** The
  allows in `WORKSPACE_PERMISSIONS` must precede the catch-all write deny. `test_permission_matrix` imports the
  live list rather than copying it, so reordering it fails the test.
- **`write_todos` is not added automatically in deepagents 0.7.1.** The middleware stack resolves from a
  per-`provider:model` harness profile, so planning may vanish just by changing the model string. `agent.py`
  pins `TodoListMiddleware()` explicitly.
- **Models need the LangChain `provider:model` prefix.** A bare `claude-opus-5` will not resolve.
  `require_api_key()` derives which key to demand from that prefix.
- **The mock's "now" is a frozen `_TODAY = date(2026, 7, 31)`**, not `date.today()`. Generation and every
  recency filter read it, so they cannot drift and silently disable the close-date screen. Changing it, or the
  square-footage sigma, shifts comp availability — `test_most_listings_have_a_usable_comp_set` asserts ≥60% of
  active listings clear the 3-comp minimum.
- **The approval-gate resume payload is a mapping, not a list:** `Command(resume={"decisions": [...]})`, with
  exactly one decision per pending tool call. The gate **fails closed** — exhausted or absent stdin rejects.
  `test_resume_payload_is_a_mapping_not_a_list` asserts on the *source text* of `main._handle_interrupts`, so
  refactoring that function can break the test without changing behaviour; keep the literal or update the test.
- **Tests monkeypatch module-level constants** (`comms.DRAFTS_DIR`, `documents.DOCUMENTS_DIR`). Those names must
  stay module globals resolved at call time — rebinding them into defaults or a local alias breaks the patching.
- **`save_draft` is the only sanctioned way to produce a draft.** Adding a second path (e.g. `write_file`) was a
  real defect: the reviewer got two divergent copies of one email.

## Conventions

- **Tool return values are JSON strings, and that JSON is prompt surface.** Key names, nulls, and the embedded
  hints are read by the model. `screened_out: null` means "not measured" and is distinct from `{}` meaning
  "nothing screened out"; `denominator` on `qualify_lead` exists to stop the model blaming a budget for a
  bedroom shortfall. Don't rename or flatten these casually.
- **Arithmetic belongs in Python, not the model.** Medians, months-of-inventory, and the indicated value range
  are computed in `tools/market.py`, and the analyst prompt tells the specialist to use them rather than
  recompute.
- **No send capability, by design.** The agent writes drafts and stops so a licensed human presses send. If you
  add real delivery, gate it through `interrupt_on` in `build_agent`.
- Every path the agent may touch derives from `PROJECT_ROOT` in `config.py` (overridable with
  `REA_PROJECT_ROOT`, which matters for non-editable installs where `parents[2]` lands in site-packages).
- `real_estate_agent/__init__.py` exposes `build_agent` through a lazy `__getattr__`, so importing providers or
  dataclasses does not drag in the LangChain stack. Keep it that way.
- `pyproject.toml` sets `pythonpath = ["."]` so `tests/` can import root-level `main.py`.

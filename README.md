# real-estate-agent

Real estate agent on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview).

An orchestrator that delegates to four specialists — property search, market
analysis, document review, and client communication — each with its own tools,
its own context window, and a shared `/workspace/` filesystem.

## Quick start

```bash
cp .env.example .env          # add your ANTHROPIC_API_KEY
uv sync
uv run python main.py "Find 3-bed homes in Round Rock under $600k"
```

Interactive mode:

```bash
uv run python main.py
```

Tests (no API calls, no key required):

```bash
uv run pytest tests/ -q
```

## Architecture

```
orchestrator  (write_todos + task)
├── property-search    search_listings, get_listing
├── market-analyst     find_comparables, market_statistics    → skills/cma-analysis
├── document-reviewer  list_documents, extract_document_text  → skills/document-review
└── client-liaison     qualify_lead, save_draft               → skills/client-comms
```

The orchestrator holds no domain tools. It plans with `write_todos`, delegates
with `task`, and synthesises what comes back. Specialists share the filesystem
but not each other's context, so a long document review can't crowd out the
shortlist the buyer agent is maintaining.

| Path | Purpose |
|---|---|
| `src/real_estate_agent/agent.py` | Orchestrator assembly, backend, permissions |
| `src/real_estate_agent/subagents.py` | The four specialist definitions |
| `src/real_estate_agent/tools/` | Tool groups, each bound to a data source |
| `src/real_estate_agent/providers/` | `ListingsProvider` protocol + mock feed |
| `skills/` | Progressive-disclosure methodology, loaded on demand |
| `workspace/` | Agent scratch space (gitignored) |

## Plugging in real listings data

**The default data source is a deterministic mock**, not a real feed. The agent
runs end-to-end today, but every listing is synthetic.

`ListingsProvider` (`providers/base.py`) is the only thing the tools depend on.
Implement `search`, `get`, and `comparables` against your feed and pass it in —
no tool, subagent, or prompt changes:

```python
from real_estate_agent import build_agent

agent = build_agent(provider=MyMlsProvider(api_key=...))
```

The mock models two deliberately different markets (Austin 78704/78745 at
$529k–$1.4M, Round Rock at $210k–$683k) so budget-feasibility logic has
something real to bite on.

## Skills vs. prompts

Three specialists load a skill; `property-search` does not. That is the
criterion working as intended — CMA adjustment grids, contract clause
checklists, and fair-housing rules for listing copy are too large to sit in a
system prompt, while property-search's workflow fits in a paragraph.

Skills are **not** inherited from the orchestrator. Each subagent that needs one
declares it explicitly in `subagents.py`.

## Safety and containment

- **Write containment.** `FilesystemBackend` grants real disk access, so
  `build_agent` passes explicit `FilesystemPermission` rules: writes are allowed
  only under `/workspace/`, `.env` and `.git` are denied for read and write, and
  everything else is read-only. Rules are evaluated **in order, first match
  wins** — the allows must precede the catch-all deny.
- **No send capability.** `save_draft` writes to `workspace/drafts/` and stops.
  A human reviews and sends. If you add real delivery, gate it:
  `build_agent(require_approval=True)` pauses on `save_draft` for approval
  (needs the checkpointer, which is on by default).
- **Path traversal.** Document filenames come from model output, so they are
  resolved and confined to the documents directory before any read.
- **No shell.** The `execute` tool appears in the tool list but errors out —
  it requires a sandbox backend, and `FilesystemBackend` is not one.
- **Fair housing.** The `client-comms` skill carries explicit prohibited-language
  rules for listing copy, and `cma-analysis` forbids demographic adjustments.

The agent is an assistant, not a licensed professional. Prompts and skills push
it to recommend an attorney, appraiser, or lender rather than substitute for one.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. |
| `LANGSMITH_API_KEY` / `LANGSMITH_TRACING` / `LANGSMITH_PROJECT` | — | Recommended. Current names; `LANGCHAIN_*` no longer works. |
| `REA_MODEL` | `anthropic:claude-opus-5` | Orchestrator. LangChain needs the `provider:model` prefix. |
| `REA_SUBAGENT_MODEL` | inherits `REA_MODEL` | Specialists. |

## Notes on deepagents 0.7.1

`write_todos` is **not** added automatically. The middleware stack resolves from
a per-`provider:model` harness profile, so planning may or may not be present
depending on the model string. `agent.py` pins `TodoListMiddleware()` explicitly
rather than depending on that resolution — `tests/` asserts it stays wired.

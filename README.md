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
- **No send capability, by design.** The agent cannot send email. `save_draft`
  writes three artifacts and stops:
  a `.md` (readable canonical copy), a `.eml` carrying `X-Unsent: 1` so a mail
  client opens it as an editable draft, and a `mailto:` URL in the return value
  for one-click compose (omitted above ~1800 chars, where clients truncate).
  A human still reads the text and presses send — which keeps accountability
  with the licensed person, where fair-housing and agency law put it.
  If you later add real delivery, gate it: `build_agent(require_approval=True)`
  pauses `save_draft` for approval (the checkpointer is on by default).
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

## Verified behaviour

Run live against `anthropic:claude-opus-5`, traces in LangSmith:

| Check | Result |
|---|---|
| Orchestrator delegates rather than answering directly | `task` at root; 6/6 correct matches |
| Subagents load their skills | ±20% size screen and 25% adjustment-drop threshold applied — both exist only in `SKILL.md` |
| Planning and cross-specialist handoff | 3× `write_todos`, 2 delegations, liaison read the analyst's CMA file |
| Write containment | `permission denied` on `/src/notes.md`; nothing written outside `workspace/` |
| Fair-housing guardrail | Refused "family-friendly", "young professionals", "safe neighborhood", "good schools" with the legal basis for each, and supplied compliant copy |
| Approval gate, both directions | Approve writes the draft; reject blocks it; exhausted/absent input **fails closed** rather than crashing or approving |

Three defects the run exposed, since fixed:

- `comparables()` ranked by similarity but never **rejected** on size, handing
  back comps the CMA methodology discards anyway. It now applies a size screen
  and reports what it filtered, so a thin comp set is distinguishable from a
  thin market.
- The mock spread square footage too widely for a 66-listing dataset, so small
  properties had no size-matched comps and no CMA was possible. Sizes now
  cluster; 32 of 40 active listings clear the 3-comp minimum, and a couple of
  genuine outliers remain so the insufficient-comps path stays reachable.
- client-liaison had two ways to write a draft and used both, producing
  divergent copies. `save_draft` is now the only sanctioned path.

A later code review found more, since fixed and regression-tested:

- **`--require-approval` was unusable.** The resume payload was a bare list,
  but the middleware reads `interrupt(request)["decisions"]` — so answering the
  prompt raised `TypeError` on both approve and reject. It also built one
  decision regardless of how many tool calls were pending, which the middleware
  rejects outright.
- **`market_statistics` divided by `months_back` without filtering by it**, so
  the same 16 sales yielded months-of-inventory of 5.2 or 21.0 — flipping the
  reading from balanced to extreme buyer's market. The window is now applied to
  the sales themselves, via a `sold_within_months` provider filter.
- **`qualify_lead` blamed the budget for a bedroom shortfall**, reporting a $2M
  budget in a $683k market as clearing "only 8% of inventory". Budget share is
  now measured against listings that already meet the non-price requirements.
- **`status="Active"` returned zero listings** — string filters were
  case-sensitive, which reads to the agent as an empty market. Now
  case-insensitive, and the tool exposes proper enums.
- **A newline in an email subject** raised after the `.md` was already written,
  leaving an orphan pointing at a `.eml` that never existed. Headers are
  flattened first and the message is built before anything is written.
- Plus: same-second drafts no longer clobber each other, text extraction is
  size-capped like the PDF branch, `lot_sqft` can't go negative, the dataset has
  one `_TODAY`, and `require_api_key` derives the needed key from the model's
  provider prefix instead of always demanding an Anthropic one.

## Notes on deepagents 0.7.1

`write_todos` is **not** added automatically. The middleware stack resolves from
a per-`provider:model` harness profile, so planning may or may not be present
depending on the model string. `agent.py` pins `TodoListMiddleware()` explicitly
rather than depending on that resolution — `tests/` asserts it stays wired.

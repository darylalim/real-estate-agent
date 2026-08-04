# real-estate-agent

[![check](https://github.com/darylalim/real-estate-agent/actions/workflows/check.yml/badge.svg)](https://github.com/darylalim/real-estate-agent/actions/workflows/check.yml)

Real estate agent on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview).

An orchestrator that delegates to four specialists — property search, market
analysis, document review, and client communication — each with its own tools,
its own context window, and a shared `/workspace/` filesystem.

## Quick start

```bash
cp .env.example .env          # add your ANTHROPIC_API_KEY
uv sync                       # Python 3.14, pinned by .python-version
uv run python main.py "Find 3-bed homes in Hilo under $600k"
```

Interactive mode:

```bash
uv run python main.py
```

Conversations are checkpointed to `workspace/checkpoints.db`, so a thread can be
picked up in a later run. Every invocation prints its thread id:

```bash
uv run python main.py --thread 8f2c1e04-...   # continue where you left off
```

## Web UI

```bash
uv run streamlit run streamlit_app.py
```

Two pages over the same project:

- **Chat** — the browser analogue of `main.py`. Drives the real orchestrator,
  shows each delegation and tool result as it happens, and lists what the
  specialists wrote to `/workspace/`. History is rendered from the checkpoint
  database rather than from browser state, so a thread id printed by the CLI can
  be pasted in and resumed here — and vice versa. The approval gate is a sidebar
  toggle, and it fails closed the same way the CLI's does: a thread paused
  mid-`save_draft` refuses to accept a new turn until the toggle is back on.
- **Market** — supply, pricing and absorption over the listings provider. No
  model runs and no API key is needed. Its headline numbers come from the
  agent's own `market_statistics` tool rather than a second implementation, so
  the dashboard cannot quietly disagree with what the analyst was told.

Both pages are themed from `.streamlit/config.toml` — native config, no CSS —
and the theme defines light *and* dark, so the mode switch in the settings menu
keeps working. Browsing a workspace artifact in the chat sidebar is an
`st.fragment`, so picking a file does not re-read the checkpoint or re-render
the conversation. Tool-result panels and the file preview render their contents
only when opened — a collapsed `st.expander` still ships its body otherwise, so
a long thread was re-sending every tool call's JSON on every turn.

Checks — all three must pass. No API calls, no key required:

```bash
scripts/check.sh              # runs all three with the pinned versions
```

Or individually:

```bash
uv run pytest tests/ -q       # 71 tests, ~0.9s
uvx ty@0.0.65 check           # type check
uvx ruff@0.16.1 check .       # lint
```

Development is pinned to Python 3.14, so the `requires-python = ">=3.11"` floor needs an explicit run —
`--isolated` keeps it out of your project venv:

```bash
uv run --python 3.11 --isolated pytest tests/ -q
```

The definition of done is "N tests, ty clean, ruff clean", and both tool versions are pinned deliberately —
each is pre-1.0, so an unpinned run can report a different result on identical source. ruff enforces its own
pin via `required-version` and will refuse to run if you drop it; ty has no equivalent, so that one is on you.
`ruff format` is deliberately **not** used here — see `CLAUDE.md`.

CI (`.github/workflows/check.yml`) runs `scripts/check.sh --floor` on Linux for every push and PR, plus
`uv sync --locked` so a stale `uv.lock` fails the build. It calls the script rather than restating the tools,
which is the point of having the script: one place decides what "done" means. No secrets — the suite is
entirely offline.

## Architecture

```
orchestrator  (write_todos + task)
├── property-search    search_listings, get_listing
├── market-analyst     find_comparables, market_statistics,
│                      search_listings, get_listing           → skills/cma-analysis
├── document-reviewer  list_documents, extract_document_text  → skills/document-review
└── client-liaison     qualify_lead, save_draft               → skills/client-comms
```

The orchestrator holds no domain tools. It plans with `write_todos`, delegates
with `task`, and synthesises what comes back. Specialists share the filesystem
but not each other's context, so a long document review can't crowd out the
shortlist the buyer agent is maintaining.

market-analyst is the only specialist with two tool groups (`subagents.py`
passes it `market_tools + listing_tools`), so it can look up a subject property
itself instead of round-tripping through the orchestrator for an id it was
already given.

| Path | Purpose |
|---|---|
| `src/real_estate_agent/agent.py` | Orchestrator assembly, backend, permissions |
| `src/real_estate_agent/subagents.py` | The four specialist definitions |
| `src/real_estate_agent/tools/` | Tool groups, each bound to a data source |
| `src/real_estate_agent/providers/` | `ListingsProvider` protocol + mock feed |
| `skills/` | Progressive-disclosure methodology, loaded on demand |
| `workspace/` | Agent scratch space (gitignored) |
| `streamlit_app.py`, `app_pages/`, `ui/` | The web UI — a consumer of the package, like `main.py` |
| `.streamlit/config.toml` | Theme, in both light and dark. No CSS anywhere in the app |

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

The mock models two deliberately different Hawaii markets (Honolulu 96815/96816
at $566k–$1.28M active, median $915k and $768/sqft; Hilo 96720 at $257k–$642k,
median $455k and $329/sqft) so budget-feasibility logic has something real to
bite on. The two Honolulu ZIPs sit ~2.1 miles apart, so the default 1.5-mile
comp radius mostly separates them — mostly, because the per-ZIP coordinate
jitter lets a small tail of cross-ZIP pairs fall inside it. Hilo is on another
island and cannot contaminate an Oahu comp set at any radius a CMA would use.

Everything that differs between ZIPs lives on one `_Market` record, because a
single global value was wrong for at least one of the three:

- **Property mix.** 96815 is Waikiki — a wall of condo towers with essentially
  no detached housing — while 96720 is overwhelmingly single-family. A uniform
  draw put 2,100-sqft single-family homes on Seaside Ave.
- **Coordinate jitter.** Waikiki is a ~0.35-mile strip between the Ala Wai Canal
  and the shoreline, and a box sized for a sprawling mainland ZIP put six of its
  listings in the Pacific, which the market page's `st.map` rendered faithfully.
- **Association fee.** Derived from size and market rather than drawn from a
  flat menu. Uncorrelated, it put $165/mo on a $1.3M Waikiki condo and $1,150/mo
  on a $380k Hilo one — and at Hawaii's fee levels that drives the affordability
  answer rather than decorating it.

Addresses carry their real street type (`Kalakaua Ave`, `Beach Walk`,
`Wilhelmina Rise`) rather than a suffix drawn at random, and street names are
scoped to their ZIP.

The dataset is deliberately sold-heavy — ~68% closed, ~22% active — because
months-of-inventory divides standing inventory by the monthly rate of a *year*
of sales, so a market at four months needs roughly three times as many closed
records as active ones. The payoff is that the two markets now land on opposite
sides of the interpretation threshold: Honolulu reads 2.8 months (seller's) and
Hilo 6.5 (buyer's), where the previous fixture reported a deep buyer's market
for both and never exercised the other branch.

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
  pauses `save_draft` for approval. The CLI's checkpointer is durable, so a
  pending approval survives the process — reject is not the same as walking away.
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

### The CLI

Run live against `anthropic:claude-opus-5`, traces in LangSmith:

| Check | Result |
|---|---|
| Orchestrator delegates rather than answering directly | `task` at root; 6/6 correct matches |
| Subagents load their skills | ±20% size screen and 25% adjustment-drop threshold applied — both exist only in `SKILL.md` |
| Planning and cross-specialist handoff | 3× `write_todos`, 2 delegations, liaison read the analyst's CMA file |
| Write containment | `permission denied` on `/src/notes.md`; nothing written outside `workspace/` |
| Fair-housing guardrail | Refused "family-friendly", "young professionals", "safe neighborhood", "good schools" with the legal basis for each, and supplied compliant copy |
| Approval gate, both directions | Approve writes the draft; reject blocks it; exhausted/absent input **fails closed** rather than crashing or approving |

### The web UI

Then run live against the same model, with approval switched on:

| Check | Result |
|---|---|
| Streaming and dedupe | Tool calls and results render as they arrive; no message printed twice |
| Delegation | `task(subagent_type=client-liaison)` from the orchestrator, never answered inline |
| Reject | Rejection reached the specialist; nothing written to `drafts/`; the orchestrator re-planned from the stated reason — `write_todos`, then `task(property-search)` with "Do not estimate or infer anything" |
| Cross-specialist handoff | property-search wrote `/workspace/documents/mls-1022-facts.md`; the orchestrator pointed client-liaison at that path, and it drafted from the real figures |
| Approve | `.md` and `.eml` both written, `X-Unsent: 1` present, real listing data, and the model flagged the pending status rather than guessing whether the contract would close |
| Workspace browser | Populated as specialists wrote; the checkpoint database stayed out of it |

Five defects, since fixed and regression-tested — three the run exposed, two a
later code review found in the same family. All five are one Streamlit rule —
**widget state is keyed and lifecycle-bound** — and none of them raised:

- **The approval toggle switched itself off.** `st.rerun()` fires from inside
  the sidebar, which aborts the run before any widget declared after it renders
  — and Streamlit drops a keyed widget's value on a run where it does not
  render. So switching approval on and then clicking "New conversation" left the
  requirement off, and the next `save_draft` would have been written unattended.
  The toggle is now declared first, with `persist_state="session"` behind it.
- **The approval form re-displayed the previous call's arguments.**
  `st.text_area(..., key=...)` stores its first value in session state and
  reuses it, so a second interrupt at the same index showed a stale payload
  while the decision applied to the live one — the form still showed a
  placeholder body after the specialist had redrafted with real figures. A
  reviewer would have approved text they never saw. Arguments now render with
  `st.code`, which holds no state.
- **"Resume a thread" had a dead button.** A bare `st.text_input` commits on
  blur or Enter, so a plain button beside it submitted the *previous* value:
  typing an id and clicking Load did nothing, silently, until you pressed Enter
  first. Both now live in one `st.form`.
- **The decision control kept the previous reviewer's answer.** The same rule as
  the argument display, one widget over — and worse, because it defeats the
  fail-closed default rather than only showing stale text. `default="Reject"`
  applies to a key Streamlit holds no value for; on any later render the stored
  value wins. So approving one call left the *next* interrupt's form already
  reading "Approve", and a reviewer who checked the new arguments and pressed
  submit approved something they never chose. Measured: `clear_on_submit=True`
  does not restore the default and deleting the key mid-run breaks the widget.
  The keys now carry a round number that advances on every submission.
- **The workspace list lagged a turn behind.** The sidebar is built near the top
  of a run, before the turn writes anything, and the page only re-ran when an
  interrupt was pending — so the answer on screen could cite a CMA by path while
  the sidebar still read "Nothing written yet". A turn now always re-runs.

Re-verified live afterwards, with the redraft loop that produces a second
interrupt: an identical filename and subject with a changed body — the exact
shape the stale-argument bug hid — displayed the new body, and the text written
on approval matched the text on screen byte for byte.

Three defects the CLI run above exposed, since fixed:

- `comparables()` ranked by similarity but never **rejected** on size, handing
  back comps the CMA methodology discards anyway. It now applies a size screen
  and reports what it filtered, so a thin comp set is distinguishable from a
  thin market.
- The mock spread square footage too widely for the dataset size, so small
  properties had no size-matched comps and no CMA was possible. Sizes now
  cluster; 21 of 25 active listings clear the 3-comp minimum, and the other 4
  remain genuine outliers so the insufficient-comps path stays reachable.
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
  budget in a sub-$800k market as clearing "only 8% of inventory". Budget share is
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

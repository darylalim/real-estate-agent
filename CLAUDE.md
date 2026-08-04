# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A real estate agent built on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) 0.7.1: an
orchestrator that holds no domain tools and delegates to four specialists over a shared `/workspace/` filesystem.

`README.md` is current and detailed — it covers the safety rationale, the live-run verification table, and the
defect history. Read it before changing containment rules, the draft handoff, or the mock dataset. This file
covers what the README doesn't: commands, the wiring that spans several files, and the invariants that fail
silently.

## Commands

**When working with Python, invoke the relevant Astral skill first**: `/astral:uv` for dependencies,
environments, and running anything; `/astral:ty` for type checking; `/astral:ruff` for linting. Each carries
that tool's current best practices, and the pins, rule set, and `ruff format` decision recorded below were all
derived by following them — so a change made without loading the skill is likely to contradict something here.

```bash
uv sync                                          # install (Python pinned to 3.14 by .python-version)
cp .env.example .env                             # then add ANTHROPIC_API_KEY

uv run python main.py "Find 3-bed homes in Hilo under $600k"
uv run python main.py                            # interactive
uv run python main.py --require-approval         # pause before save_draft
uv run python main.py --thread <id>              # continue a conversation (persisted to workspace/)

uv run streamlit run streamlit_app.py            # the same agent in a browser, plus a market dashboard

scripts/check.sh                                 # the whole definition of done
scripts/check.sh --floor                         # the above, plus the 3.11 leg

uv run pytest tests/ -q                          # full suite: 73 tests, ~1.3s, no API calls
uv run pytest tests/test_real_estate_agent.py::test_permission_matrix -q   # one test
uv run pytest -q -k "traversal"                  # by keyword
uv run --python 3.11 --isolated pytest tests/ -q  # the requires-python floor

uvx ty@0.0.65 check                              # type check — pinned; not a declared dependency
uvx ruff@0.16.1 check .                          # lint — pinned; rule set in pyproject.toml
```

**Prefer `scripts/check.sh`.** It is where the pins are enforced rather than described: a version inside a
Markdown fence binds whoever reads the fence, the script binds whoever runs it. The individual commands above
still work and are worth knowing, but the script is the one to run before saying you are done.

**Definition of done in this repo is "N tests, ty clean, ruff clean"** — most commit messages end with some
form of it, though not all: `9227e4d` has no done-line and `da55993` ends "21 tests, no API calls. Live
end-to-end behaviour is unverified." Follow it going forward rather than reading it as an invariant already
holding over the whole history. All three parts are pinned so the phrase means the same thing on any machine:
`.python-version` fixes the interpreter at 3.14 (`requires-python` alone only sets a floor, so a fresh
`uv sync` elsewhere would pick whatever newest interpreter it found), and the `ty` and `ruff` versions are
pinned **in the command** rather than added to `pyproject.toml` as dependencies — `uvx` runs each in its own
ephemeral environment, so neither one's dependencies enter `uv.lock`, but that also hides the version. Both
are pre-1.0 and their diagnostics move fast; unpinned, "clean" can flip between two runs on identical source.

**ruff's pin is self-enforcing; ty's is enforced by the wrapper.** `required-version = "==0.16.1"` in
`pyproject.toml` makes a mismatched ruff hard-fail (exit 2) rather than silently applying a different rule
set — verified against 0.15.0. ty has no such setting, so its pin holds only for callers who go through
`scripts/check.sh`; `uvx ty check` typed directly still resolves whatever is newest. That is a real gap, just
a narrower one than before, and `test_check_script_pins_match_the_docs` at least stops the script and the docs
disagreeing. CI now runs the same script on every push and PR, so the pin holds off this machine too — see
below. Bump a pin deliberately, don't drop it. `pytest` is the only dev dependency, and `.gitignore` already
ignores `.ruff_cache/` and `.ty_cache/`.

**`ruff check` yes, `ruff format` no.** The formatter would rewrite 7 of the 14 Python files — line-wrapping
disagreements, not defects — and bury real diffs under cosmetic ones. Lint only. (`ruff format --check .`
reports a total of 19, not 14: since 0.16 it also formats Python fences inside Markdown, so `README.md`,
this file, and the three `SKILL.md` files are in its denominator. All 7 rewrites are `.py`.) A consequence
worth knowing: blank-line and whitespace structure has no gate at all, since `E3` is preview-only in 0.16.1
and the formatter is the only other thing that would catch it.

**The rule set is `extend-select`, not `select`.** Replacing the defaults outright looks like it protects
against them widening across releases, but `required-version` already fixes which defaults apply, so the
replacement bought nothing and silently dropped ~300 rules the codebase already passes — including `B006`,
`BLE001`, and `DTZ005`. Add rules here; don't take the defaults away. A prior version of this file claimed the
function-scoped imports in `tests/` exist so `monkeypatch` can reach module globals. **That is false** — the
test module already imports `real_estate_agent.tools.comms` at module scope, so the module object is in
`sys.modules` regardless, and `monkeypatch.setattr` rebinds the same global either way. Those imports can be
hoisted; only `documents.py`'s lazy `pypdf` and `__init__.py`'s lazy `build_agent` are deliberate.

**Run the 3.11 leg before changing anything the type system touches.** `.python-version` pins development to
3.14, so without it the `requires-python = ">=3.11"` floor is never executed on any machine — the promise
would be checked only by ty's syntax-level view, never at runtime. `--isolated` builds a throwaway environment
so your 3.14 venv is untouched; it costs one resolve, then ~3s. If it ever fails, the honest fix is usually to
narrow `requires-python`, not to delete the leg.

The whole suite runs offline **because `tests/conftest.py` makes it so** — it was not, and the claim on this
line was false for the repo's first 31 commits. `test_agent_exposes_planning_and_delegation` builds the real
graph under a dummy key to assert on wiring only, so no test reaches Anthropic; but `config.py` calls
`load_dotenv()` at import, `.env` carries `LANGSMITH_TRACING=true`, and every `tool.invoke()` in the suite was
therefore a LangSmith **root** run — 17 of them per run, over a network, billed against a free tier. Read the
conftest before touching any of that; the ordering it depends on is easy to undo. There is no reason to skip
tests for cost or network *now*, which is a different statement from the one that used to be here.

The cluster at the end of the file is of a different kind: those tests check the *toolchain config*, not the
agent, and fail when the config and the docs disagree. Four read the live `pyproject.toml` and the pins written
in this file and `README.md` — the ruff pin, `extend-select`, and `error-on-warning` are otherwise all silently
droppable. `test_check_script_pins_match_the_docs` extends that to `scripts/check.sh`, which is the thing that
actually runs them. Each was verified to fail on the drift it describes, not merely to pass today.
(No count here on purpose: the two counts this file used to write out both went stale the first time the list
grew, and a positional "the last six" is the same trap wearing a different hat.)

`test_the_suite_does_not_trace_to_langsmith` sits in that cluster but reads neither config nor docs — it asks
`langsmith.utils.tracing_is_enabled()` about the live environment. Asserting on the effective state rather than
on `tests/conftest.py` existing is deliberate: deleting that file, renaming a variable inside it, or adding
`override=True` to `config.py`'s `load_dotenv()` all defeat the fix, and only the environment sees all three.

`test_documented_test_count_matches_the_suite` is the odd one out: it reads no config at all, and it counts
itself via `request.session.testscollected`, because adding a test is precisely when the number written above
goes stale. It skips on anything that collects a subset — `-k`, `-m`, `--deselect`, `--lf`, `--ff`, `--sw`, or
an explicit node id — so the count is enforced by full-suite runs and by nothing else. The `--lf` case is the
one to keep in mind: without that skip, a filtered run fails the test, which puts it in the last-failed set,
which makes the next `--lf` collect only it. That is a red `--lf` no code change clears.

## CI

`.github/workflows/check.yml`, one job on `ubuntu-latest`, triggered by push to `main`, any PR, and
`workflow_dispatch`. It **calls `scripts/check.sh --floor`** rather than re-listing ruff, ty and pytest as
YAML steps — spelling them out would make the workflow a fourth home for the pins, and the one home
`test_check_script_pins_match_the_docs` does not read. Change what CI runs by changing the script.

Three things it buys that nothing else here does:

- **A machine that is not yours.** The `.claude/` hooks below run on one laptop; a clone gets none of them.
- **Linux, and a clean checkout.** Development is macOS with a warm `.venv`.
- **`uv sync --locked`.** It fails rather than re-resolving, so this is the only check anywhere that
  `uv.lock` is still current with `pyproject.toml`. `uv run` inside `check.sh` syncs too, but silently.

`--floor` is on in CI and opt-in locally: the 3.11 leg costs a resolve you don't want on every Stop hook, and
running it somewhere is most of the reason to have a second machine.

No secrets, by construction. The suite is offline and the one test that builds the real graph injects its own
dummy `ANTHROPIC_API_KEY`, so `permissions: contents: read` is enough — don't add a key to make some future
live test possible without re-reading that decision. A side effect worth knowing: because a clean checkout has
no `.env`, CI never emitted a LangSmith trace even while every local run was emitting 17. The whole spend was
on this laptop, via the Stop hook, which is also why nothing in the workflow logs pointed at it. Actions are pinned by commit sha with the tag in a
trailing comment; bump both halves together.

## The hooks in `.claude/`

Five hooks, checked in, active only inside Claude Code. They are **local convenience, not a gate anyone else
inherits** — a clone without Claude Code gets none of them, which is why the enforcement that matters lives in
`pyproject.toml`, `scripts/check.sh`, `tests/`, and now CI.

| Hook | Event | Does |
|---|---|---|
| `session-start.sh` | SessionStart | Records the starting commit, so the Stop gate can see work committed mid-turn |
| `static-gate.sh` | PostToolUse(Edit\|Write) | ruff + ty on `.py` edits, 0.12s |
| `protect-files.sh` | PreToolUse | Denies `.env` and `uv.lock` via Edit/Write/NotebookEdit, and `.env` reads via Bash |
| `confirm-live-run.sh` | PreToolUse | Asks before `main.py` and `streamlit run` — the two commands that reach the model |
| `done-gate.sh` | Stop | `scripts/check.sh` always; the 3.11 floor leg when Python changed |

Both `PreToolUse` hooks share one registration in `.claude/settings.json` — a single
`"matcher": "Edit|Write|NotebookEdit|Bash"` block listing both scripts — so `confirm-live-run.sh` is invoked on
Edit and Write too, not just Bash. It exits 0 when there is no command to inspect, so this costs nothing, but
don't read the table above as saying each hook sees only the tools it cares about.

Four more things worth knowing before editing:

- **`done-gate.sh` runs `check.sh` unconditionally.** It used to gate on a changed `*.py`, which skipped the
  suite on exactly the edits the toolchain tests exist to catch — a `pyproject.toml` that drops
  `required-version`, or a stale count in this file. 1.7s is cheap enough not to need the cleverness.
- **The Stop gate stands down after three consecutive blocks.** Exit 2 on Stop forces the turn to continue, so
  a failure no code change fixes — the floor leg needs the network — would otherwise loop forever.
- **`protect-files.sh`'s Bash arm is a speed bump, not a boundary.** `E=.env; cat $E` defeats it. It exists for
  the accidental `cat .env`, not for an adversary. An earlier version of this hook layer also tried to police
  `ruff format` and the ty pin by matching shell strings; that was deleted rather than patched, because
  `cat x && uvx ruff format .` walked straight through it and the guard read as protection it did not provide.
- **`confirm-live-run.sh` is leaky too, and that is fine, because it asks rather than denies.** The distinction
  is the whole reason it survived the deletion above: a missed case costs one unprompted run, not a false
  belief that something is blocked. It normalises quotes and whitespace first — `uv run main.py`,
  `uv run python -m main`, and `python "main.py"` were all bypasses in the first version. `M=main.py;
  uv run python $M` still gets through, and no regex over a shell string will fix that.

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

### The Streamlit app

`streamlit_app.py` (a two-page `st.navigation`), `app_pages/chat.py`, `app_pages/market.py`, `ui/`, and
`.streamlit/config.toml`.

**All of these live outside `src/real_estate_agent/`, deliberately.** The package exposes `build_agent` through a
lazy `__getattr__` so importing a provider does not drag in LangChain; putting Streamlit imports inside it
would undo that for every consumer, the CLI included. The app is a consumer of the package exactly as
`main.py` is — which also means `pythonpath = ["."]` is what lets `tests/` reach `ui/`, the same line that
already lets it reach `main.py`.

What is not obvious (both counts here used to be written out, and both went stale the first time the list
grew — in a file three tests exist to keep honest, don't reintroduce them):

- **The checkpointer is built differently here.** `main.py` uses
  `with SqliteSaver.from_conn_string(...)`, which closes the connection on exit. A Streamlit script reruns top
  to bottom on every interaction, so that shape cannot survive; `ui/agent_session.get_agent` holds a
  `sqlite3.connect(..., check_same_thread=False)` under `@st.cache_resource` instead. Same flag
  `from_conn_string` sets internally, same `ensure_workspace()`-before-connect ordering, same reason.
- **`get_agent` is keyed on `require_approval`.** That flag decides whether the `save_draft` interrupt is in
  the middleware stack at all, so toggling it has to yield a different graph rather than the cached one.
- **No Altair.** Its fluent builder (`alt.Chart(...).mark_bar().encode(...)`) is opaque to ty, which reports
  `unresolved-attribute` on `.encode` — and `error-on-warning = true` makes that a failure. `market.py`
  pre-bins prices in plain Python and uses `st.bar_chart`. Reach for a native chart before an ignore comment.
- **One provider, in `ui/provider.py`, for both pages.** `get_agent` passes it explicitly rather than letting
  `build_agent` fall back to its own `MockListingsProvider`. Two instances of the deterministic mock are
  indistinguishable — which is why the split survived review — but a real feed would put the analyst and the
  dashboard on different snapshots, and taking the agent live would need two edits, not the one the README
  promises.
- **`thread_snapshot` is one checkpoint read.** `get_state` takes the saver's lock and deserialises the whole
  message list on every call, and a Streamlit script re-runs for *every* interaction, including ones with
  nothing to do with the agent. `stored_messages` and `pending_actions` remain as thin wrappers because the
  tests use them; the page itself takes the snapshot.
- **`market.py` imports its thresholds from `tools/market.py`.** The seller/balanced/buyer badge and the
  `interpretation_hint` printed at the foot of the same page would otherwise be two copies of 4 and 6.
- **The sidebar's workspace browser is an `@st.fragment`.** Its selectbox and preview expander are pure
  viewers, but a widget outside a fragment reruns the whole script — which on this page means
  `thread_snapshot` (the checkpointer's lock, plus deserialising every message) and a full transcript
  re-render, to answer a question about a file. A fragment confines that to itself. Two constraints come
  with it: the fragment must be *called* inside the `with st.sidebar:` block and after that block's first
  write, or Streamlit has no container to redraw into. The turn-end rerun is app-scoped and reruns fragments
  too, so the "a turn always re-runs the page" invariant above still holds.
- **Theming is `.streamlit/config.toml`, and a misplaced key there is *discarded*, not rejected.** Three
  traps, all silent, all hit on the first attempt:
  - A custom theme with only `[theme]` removes the light/dark switch from the settings menu. Every one of
    the skill's twelve bundled templates ships exactly that, so starting from one costs you the switch.
    Colour lives in `[theme.light]` / `[theme.dark]` here (plus their `.sidebar` sub-tables).
  - **`chartCategoricalColors` is registered at `[theme]` only.** Under `[theme.light]` it logs "not a
    valid config option" to stderr and is dropped — the app starts, looks styled, and charts fall back to
    the built-in palette. So the chart palette **cannot** differ between modes; the one in the file is
    picked to clear 3:1 on both backgrounds. `test_every_theme_setting_is_a_real_config_option` asks
    `st.get_option` about every leaf key rather than keeping a second list of what is valid.
  - **Heading sizes set in `[theme]` are not inherited by the sidebar** — Streamlit's own option
    description says so. `[theme.sidebar]` restates them.

  No CSS anywhere; `st.markdown(unsafe_allow_html=True)` and `st.html` are not how this app is styled.
- **`market.py` drops columns before `st.dataframe`, rather than hiding them with `column_config`.**
  `{name: None}` hides a column in the browser and still serialises every value into the payload. Note the
  trade: `frame.drop(columns=...)` raises `KeyError` on a name that is not there, so `_NOT_IN_THE_TABLE` is
  checked against a real frame by `test_the_listings_table_drops_columns_rather_than_masking_them`.
  A stable `key=` on that table was tried and **dropped**: the general advice is that an unkeyed
  dataframe's identity includes its data, so a filter change remounts it and loses the reader's sort —
  but measured on 1.60, sorting by price and then switching property type kept the sort *with and
  without* the key, on a clean server restart each way. Don't re-add it on the strength of the advice
  alone; this table takes the default `on_select="ignore"`, and whatever the rule applies to, it is not
  this. Re-measure if that argument ever changes.
- **A collapsed `st.expander` still renders its body, and making one lazy makes it a widget.** Both halves
  live in `ui/elements.py` (`lazy_expander`, `stable_key`) rather than beside each call site, because the
  second half is easy to get exactly backwards. Two of the app's three expanders are lazy: the transcript's
  tool-result panels (up to `_TOOL_RESULT_PREVIEW` chars of JSON *per tool call*, re-sent on every full
  rerun and growing with the thread) and the sidebar's file preview. The third, `Resume a thread`, is
  **deliberately not** — it wraps a form, and a lazily rendered form widget loses its state, which is the
  dead-Load-button defect the README already lists.
  - `on_change="rerun"` is what makes `.open` a boolean; under the default `"ignore"` it is `None`, so
    dropping the flag makes every guard false and results stop rendering *at all*.
  - **A lazy expander needs a key that varies, and a constant key is worse than none.** Measured on 1.60:
    two same-label stateful expanders raise `StreamlitDuplicateElementId` and the page renders nothing; a
    shared constant key raises `StreamlitDuplicateElementKey` instead. Identity is the **parameter tuple,
    never the position** — an earlier version of this file said the opposite. The panel label is
    `name · N chars`, so two `search_listings` results of equal length collide; the preview's label is the
    constant `"Preview"`, so without a per-file key one file's open state applies to the next one picked.
    Hence `stable_key("tool", message_key(...))` and `stable_key("preview", chosen)`.
  - The costs differ by site. In the fragment the rerun is fragment-scoped — but not free: it re-globs the
    workspace and re-reads the file, where before the toggle reached the server at all. In the transcript
    it is a full rerun (`thread_snapshot` plus a re-render), won by transcripts being appended to far more
    often than read back. Revisit the guard, not the size cap, if that changes.
  - One consequence not covered by a test: the transcript sits above the approval form, so expanding a
    panel mid-review now reruns the page under an unsubmitted form. The decision and reason widgets keep
    their keys across that rerun, so their instances should persist — but this is reasoning, not a
    measurement, and `AppTest` commits form widgets directly and cannot model the client-side buffer.
- **Every module-level function in `ui/market_data.py` is cached.** On the mock they are free in-memory
  scans, which is exactly why an uncached one survives review — but `get_provider` is the seam the README
  promises you can point at a real feed in one edit, and on that day an uncached reader is a network
  round-trip per slider drag. `test_every_market_data_function_is_cached` asserts over *every* function
  rather than the ones calling `get_provider` by name: the name-matching version could not see a reader
  that reached the provider through a new helper, and passed by not looking. It checks the `ttl`/
  `max_entries` bound too, which the previous version only promised in its failure message.
  `state_for_city` is gone — `dataset_choices` returns the city→state map off the scan it was already
  doing, rather than a second lookup with a second cache to keep warm.

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
- **`tests/conftest.py` must disable LangSmith tracing, and it only works from there.** `config.py` calls
  `load_dotenv()` at import, so importing the package is enough to pull `LANGSMITH_TRACING=true` out of a real
  `.env`. Every `tool.invoke()` in the suite then becomes a **root** run, and LangSmith bills per trace rather
  than per span — so 17 one-span traces per run cost what 17 whole agent conversations would, while a live
  turn nests all its subagent and tool spans inside a single trace for free. `done-gate.sh` runs the suite
  every turn and the floor leg runs it twice, which is how a free tier goes in a few hundred turns. Two things
  hold it up and both are easy to undo: pytest imports `conftest.py` before any test module, so it beats the
  first `import real_estate_agent.config`; and `load_dotenv()` defaults to `override=False`, so an
  already-set variable wins — passing `override=True` there re-enables tracing everywhere at once.
  `test_the_suite_does_not_trace_to_langsmith` asserts the effective state, not the file.
- **The mock's "now" is a frozen `_TODAY = date(2026, 7, 31)`**, not `date.today()`. Generation and every
  recency filter read it, so they cannot drift and silently disable the close-date screen. Changing it, or the
  square-footage sigma, shifts comp availability — `test_most_listings_have_a_usable_comp_set` asserts ≥60% of
  active listings clear the 3-comp minimum.
- **The mock's *draw count* is load-bearing, not just its seed.** `_build_dataset` is reproducible because a
  fixed seed is consumed in a fixed order, so changing the *parameters* of an `rng` call is safe while changing
  the *number* of calls reshuffles every listing after it. Two traps: `random.choice` rejection-samples through
  `_randbelow(len(seq))`, so a list of a different length consumes a different number of raw draws — which is
  why each market's street tuple is exactly ten entries — and the `lot_sqft` and `hoa_monthly` draws are
  *conditional* on `property_type`, so they are not a constant offset. Measured: folding the street suffix into
  the name removed one draw per listing and moved the status split from 35/27/4 to 26/29/11, which broke the
  price-asymmetry invariant and put 29 cross-ZIP comps where there had been one. Nothing raised. If you must
  change the draw count, re-derive in the same commit: the ≥60% comp share, the asymmetry thresholds in
  `test_expensive_market_can_return_nothing`, and every dataset number written into `README.md`.
- **The fixture is sold-heavy on purpose, and that is what makes months-of-inventory readable.** MOI divides
  standing inventory by the monthly rate of a *year* of sales, so at a 12-month window it is
  `12 × active / sold` — a market at four months needs roughly three closed records per active one. No
  status split near 50/50 can express that at any dataset size, which is why the earlier 45/13/42 reported
  10–11 months for Honolulu, a city that has run 3–5 for years. The arithmetic in `market_statistics` was
  never wrong; the fixture simply had no year of sales behind it. `_PER_MARKET` is 36 rather than 22 for the
  same reason — holding that ratio at the old size left Hilo with four active listings to search.
- **Test thresholds that sit on a boundary test the boundary, not the behaviour.**
  `test_qualify_lead_does_not_blame_budget_for_a_bedroom_shortfall` needs `meets_requirements / total_active`
  under the tool's 0.25 cut. Its old Hilo/5-bed pairing landed on exactly 0.250 after a reshuffle and failed
  on a `<` — the signal it asserts was correct throughout. Prefer an input with room on both sides, and say in
  the docstring what the margin is, so the next retune fails for a real reason.
- **`main.py` must pass a checkpointer explicitly; the `build_agent` default is per-process.** `build_agent`
  falls back to `InMemorySaver()`, which is right for tests and wrong for the CLI — with it, `--thread <id>`
  resumed nothing across runs and the only symptom was a printed id that looked like it meant something.
  `main.py` now opens `SqliteSaver.from_conn_string(CHECKPOINT_DB)` for the whole session. Two consequences:
  the `with` block must wrap every `_turn`, because the connection closes on exit; and `ensure_workspace()`
  has to run *before* it, since `from_conn_string` will not create the parent directory.
  `test_cli_builds_with_a_durable_checkpointer` pins it, because the thread tests pass their own saver and so
  stay green if `main.py` reverts to the default.
- **`CHECKPOINT_DB` is denied in `WORKSPACE_PERMISSIONS`, and the deny must stay first.** It lives under
  `workspace/` because that is gitignored — but gitignored is not out of reach, and `/workspace/**` is the one
  subtree the agent can *write*. Without a deny ahead of that allow the model can truncate its own history
  mid-session, and `read_file` on the db returns every other thread's transcript. The pattern is
  `/workspace/checkpoints.db*` so SQLite's `-wal` and `-shm` sidecars are covered too. This was a real defect,
  shipped and caught in review: the placement was reasoned about against git and not against the rule list.
- **The approval gate must not fail open across processes.** A durable checkpointer lets an interrupt outlive
  the run that raised it, but `interrupt_on` is only wired when `--require-approval` is passed — so resuming
  such a thread without the flag would hand the graph a pending `save_draft` with no middleware left to stop
  it. `main` checks `_pending_approvals` before sending any turn and exits 1 rather than proceeding. Draining
  it from inside `_turn` alone is not enough: that runs *after* `_pump` has already sent the new message.
  **The ordering half of that went unchecked until the `ast` conversion**, on both entry points — the old tests
  asserted the guard existed and exited, not that it ran first, and both stayed green with a turn spliced in
  above the check. Each test now compares the guard's line number against the first `_turn` / `stream_turn`
  call, which is the assertion the prose above was already making.
- **Resuming replays the stored messages, so `_already_rendered` seeds the dedupe set.** `stream_mode="values"`
  re-emits the entire message list on every chunk and `main._pump` suppresses repeats with a `seen` set keyed
  on `message.id`. That set starts empty in a new process, so without seeding it from the checkpoint the whole
  prior transcript reprints before the new turn. It must key messages **exactly** as `_pump` does
  (`message.id or repr(message)`) or the dedupe misses. `test_a_thread_survives_a_rebuild` pins both halves.
- **The approval-gate resume payload is a mapping, not a list:** `Command(resume={"decisions": [...]})`, with
  exactly one decision per pending tool call. The gate **fails closed** — exhausted or absent stdin rejects.
  `test_resume_payload_is_a_mapping_not_a_list` asserts on the *source text* of `main._handle_interrupts`, so
  refactoring that function can break the test without changing behaviour; keep the literal or update the test.

**Source-text tests fail in two directions, and only one of them is loud.** The bullet above is the loud one:
the literal goes missing and the test goes red. The quiet one is a *bounded* search — `source.split(marker,
1)[0]` — where the marker is what stops the search running past the region under test. `str.split` on an absent
separator returns the whole string, so when a refactor removes the marker the bound silently widens to "the rest
of the file" and the test keeps passing on evidence from somewhere else entirely. That is exactly what happened
to `test_the_chat_page_refuses_a_thread_paused_without_the_gate`: its bound was `history = stored_messages`,
which left the page when the two checkpoint reads were consolidated into `thread_snapshot`, and from then on the
approval form's own `st.stop()` satisfied a test about the fail-closed guard. Measured: deleting the guard's
`st.stop()` outright left it green. **Prefer `ast` over source text for anything asserting where a statement
sits** — a tree has no bound to go stale, and cannot mistake prose for code.

That second half is not hypothetical either. `test_the_approval_toggle_renders_before_anything_that_can_rerun`
compared the position of `key="require_approval"` against the first `st.rerun()` in a copy of the source with
whole-line comments stripped — and nothing else. Docstrings and trailing comments stayed in, so *writing about*
a rerun above the toggle turned it red with no behavioural change. That happened the moment `_workspace_browser`
was added above the toggle with an accurate docstring, and the workaround was a rule in this file telling humans
what they were not allowed to write. The tree version has no opinion about prose, and the rule is gone.
`test_the_workspace_browser_is_a_fragment` went the same way: `"@st.fragment" in source` would have passed with
the decorator on any function in the file, so it now reads the `decorator_list` of the one that matters.

Both fail-closed gate tests read the tree now, through one shared `_sole_guard(scope, *words)` helper: it finds
the single `if` under `scope` whose condition mentions every one of `words`, matching on *identifiers* rather
than a rendered string so reformatting the condition is not a change in what it guards. Converting the CLI's
`test_resuming_a_pending_approval_without_the_flag_refuses` turned up a second gap on its own — see the
ordering note below. `test_resume_payload_is_a_mapping_not_a_list` is still a text assertion; it checks for a
literal in a function rather than a statement's position, which is the case text handles fine.
- **Tests monkeypatch module-level constants** (`comms.DRAFTS_DIR`, `documents.DOCUMENTS_DIR`). Those names must
  stay module globals resolved at call time — rebinding them into defaults or a local alias breaks the patching.
- **`save_draft` is the only sanctioned way to produce a draft.** Adding a second path (e.g. `write_file`) was a
  real defect: the reviewer got two divergent copies of one email.
- **`ui.agent_session.message_key` and `main._message_key` must stay identical.** Both dedupe a
  `stream_mode="values"` stream, and both read the same checkpoint database — so a thread started in the CLI
  and reopened in the browser reprints its entire history if they drift.
  `test_streamlit_keys_messages_exactly_as_the_cli_does` pins the pair, not either one alone.
- **The web workspace browser must never list `CHECKPOINT_DB`.** The sidebar wires each file it finds to a
  download button, and the checkpoint holds every thread's transcript — the same disclosure
  `WORKSPACE_PERMISSIONS` denies the agent, by a route those rules do not cover. `workspace_artifacts` filters
  on an allow-list of readable suffixes, so adding `.db` to it opens the hole;
  `test_the_workspace_browser_never_offers_the_checkpoint_store` covers the sidecars too.
- **The chat page's approval gate fails closed, like `main`'s.** Opening a thread that is paused mid-`save_draft`
  with the sidebar toggle off would hand the graph a pending call with no middleware left to stop it, so the
  page refuses to send rather than proceeding, and any decision that is not an explicit "Approve" — including a
  deselected control — is a reject. `test_the_chat_page_refuses_a_thread_paused_without_the_gate` reads the
  page's **syntax tree**, not its text: it finds the guard and asserts `st.stop()` is a direct child of its
  body. A nested one would be reachable rather than certain, which is the distinction the gate is.
- **The approval form must render its arguments with a *stateless* element.** Keyed widgets persist: an
  `st.text_area(..., key=f"arg_{i}_{name}")` writes its first value into `session_state` and reuses it, so a
  second interrupt at the same index showed the **previous** call's arguments while the decision applied to the
  new one — a reviewer approving text that was never going to be written. Found in a live run, where the form
  still showed a placeholder body after the specialist had redrafted with real figures. `st.code` holds no
  state; `test_the_approval_form_shows_live_arguments_not_stored_ones` forbids the key.
- **The approval toggle must be declared before anything in the sidebar that calls `st.rerun()`.** Same
  Streamlit rule, worse consequence: a keyed widget's value is dropped on any run where it does not render, and
  `st.rerun()` inside the sidebar aborts the run before later widgets get there. With the toggle last, clicking
  "New conversation" or loading a thread switched the approval requirement **off by itself**, and the next
  `save_draft` would have gone through unattended. Order is the fix, `persist_state="session"` the second line
  of defence, and `test_the_approval_toggle_renders_before_anything_that_can_rerun` pins both.

- **The decision control must get a fresh key for each approval round.** The worst instance of the rule above,
  because it defeats the gate rather than merely mis-displaying it: `default="Reject"` applies only to a key
  Streamlit holds no value for, so after one approval the *next* interrupt's form already read "Approve" and a
  reviewer who checked the arguments and pressed submit approved something they never chose. Measured:
  `clear_on_submit=True` does **not** restore the default, and `del st.session_state[key]` after the widget has
  rendered breaks the widget. `approval_round` advances on every submission so the next form gets keys that
  have never been seen. `test_the_decision_control_gets_a_fresh_key_for_each_approval_round` pins both halves.
- **A turn always re-runs the page.** The sidebar's workspace list is built near the top of a run, before the
  turn writes anything, so a conditional re-run left the answer citing a CMA by path while the sidebar still
  said "Nothing written yet". The cost is one repaint of content re-read from the checkpoint.

**The Streamlit lesson behind all five:** widget state is keyed and lifecycle-bound. If a keyed widget does
not render on a run its value is discarded; if it does render with the same key, the *stored* value wins over
the `value=`/`default=` you passed. Neither is an error. Prefer stateless elements for anything you are only
displaying, render anything load-bearing before the first `st.rerun()`, and give a widget a key that changes
when the thing it is asking about changes.

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

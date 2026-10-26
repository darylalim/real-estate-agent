"""Run the agent over an uploaded dataset and grade it in LangSmith.

    uv run python -m evals.run_experiments                       # both datasets
    uv run python -m evals.run_experiments --dataset guardrails --limit 2
    uv run python -m evals.run_experiments --scenario cma-no-comps
    REA_MODEL_MARKET_ANALYST=... uv run python -m evals.run_experiments --label B

A configuration is the ``REA_*`` variables on the command line, never a flag:
``config.py`` reads them at import. ``load_dotenv`` fills in any the command
line leaves unset from ``.env``, so a configuration meant as "the defaults" has
to set every model and effort variable explicitly -- check the plan this prints
before confirming.

**Every example is a live multi-agent run, billed.** This asks before spending,
with an estimate, unless ``--yes`` is passed. ``.claude/hooks/confirm-live-run.sh``
asks before ``main.py`` and ``streamlit run`` and knows nothing about this
module, so that prompt is this file's job.

Needs ``ANTHROPIC_API_KEY`` (the agent and the judge) and ``LANGSMITH_API_KEY``,
and the datasets uploaded first with ``python -m evals.upload_datasets``.

This is an entry point, and the import order is the same constraint ``main.py``
documents: ``config.py`` evaluates ``PROJECT_ROOT`` at import, so ``.env`` and
the temp root must both be in the environment before anything imports the
package -- ``evals.*`` modules included, since they import it in turn.
``test_the_eval_entry_points_set_their_environment_before_importing_the_package``
pins that.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from evals import confirm

REPO_ROOT = Path(__file__).resolve().parents[1]

# Measured 2026-10-25 by LangSmith's accounting on the shipped configuration
# (step 4's B-high: Opus 5.5 orchestrator and analyst, Sonnet 5.5 search,
# reviewer and liaison, no effort sent): $0.06 for the cheapest delegating
# example (fair-housing copy) to $0.52 for a CMA followed by an offer email,
# over 21 runs. A range, not a distribution -- an order of magnitude; re-measure
# after a model or effort change. All-Opus ran about 1.5x this.
_COST_PER_EXAMPLE = (0.06, 0.52)
# Every role's model, and that no effort was set, as model_metadata() reports them.
_MEASURED_ON = {
    "model": "anthropic:claude-opus-5-5",
    "model_property_search": "anthropic:claude-sonnet-5-5",
    "model_market_analyst": "anthropic:claude-opus-5-5",
    "model_document_reviewer": "anthropic:claude-sonnet-5-5",
    "model_client_liaison": "anthropic:claude-sonnet-5-5",
    "effort": None,
    "effort_property_search": None,
    "effort_market_analyst": None,
    "effort_document_reviewer": None,
    "effort_client_liaison": None,
}


def _isolated_root() -> Path:
    """A throwaway project root holding a copy of ``skills/`` and nothing else.

    Copied rather than symlinked: the agent reads skills through a
    ``FilesystemBackend`` rooted here, and a link whose target resolves outside
    the root is exactly what a containment check exists to refuse.
    """
    root = Path(tempfile.mkdtemp(prefix="rea-eval-"))
    shutil.copytree(REPO_ROOT / "skills", root / "skills")
    return root


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the agent over an uploaded dataset and grade it in LangSmith."
    )
    parser.add_argument(
        "--dataset",
        choices=["scenarios", "guardrails", "all"],
        default="all",
        help="which uploaded dataset to run (default: all)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="run only the first N examples of each"
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        metavar="ID",
        help="run only this scenario id (repeatable); ignores --limit",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="name this configuration (e.g. A, B, C): appended to the experiment name and recorded in its metadata",
    )
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)

    load_dotenv()
    root = _isolated_root()
    # After load_dotenv, so a REA_PROJECT_ROOT in .env cannot point the
    # workspace-emptying target at a real checkout.
    os.environ["REA_PROJECT_ROOT"] = str(root)
    try:
        return _run(args)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run(args: argparse.Namespace) -> int:
    """Everything after the environment is set. Imports the package; see the docstring."""
    from langsmith import Client, evaluate

    from evals.build_datasets import DATASETS
    from evals.evaluators import JUDGE_MODEL, evaluators_for, make_judge
    from evals.target import make_target
    from real_estate_agent.agent import build_agent
    from real_estate_agent.config import (
        DEFAULT_EFFORT,
        DEFAULT_MODEL,
        ROLE_EFFORTS,
        ROLE_MODELS,
        require_api_key,
    )

    try:
        require_api_key()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    if not os.getenv("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY is not set.", file=sys.stderr)
        return 1

    client = Client()
    stems = list(DATASETS) if args.dataset == "all" else [args.dataset]
    plan = {}
    for stem in stems:
        name = DATASETS[stem][0]
        if not client.has_dataset(dataset_name=name):
            print(
                f"Dataset {name!r} is not in LangSmith. Upload it first; "
                "`python -m evals.build_datasets` prints the command.",
                file=sys.stderr,
            )
            return 1
        if args.scenario:
            # Selected on metadata, which only `evals.upload_datasets` writes:
            # the CLI's upload drops it, so a CLI-uploaded dataset matches nothing.
            examples = [
                example
                for scenario in args.scenario
                for example in client.list_examples(
                    dataset_name=name, metadata={"scenario": scenario}
                )
            ]
        else:
            examples = list(client.list_examples(dataset_name=name, limit=args.limit))
        if examples:
            plan[stem] = examples

    if args.scenario:
        found = {
            (example.metadata or {}).get("scenario")
            for examples in plan.values()
            for example in examples
        }
        if missing := sorted(set(args.scenario) - found):
            print(
                f"No example has scenario {missing}. Check the id against "
                "evals/datasets/, and that the datasets were uploaded with "
                "`python -m evals.upload_datasets` -- the CLI drops metadata.",
                file=sys.stderr,
            )
            return 1

    total = sum(len(examples) for examples in plan.values())
    low, high = (total * cost for cost in _COST_PER_EXAMPLE)
    specialists = ", ".join(f"{role} {spec}" for role, spec in ROLE_MODELS.items())
    label = f"configuration {args.label}: " if args.label else ""
    print(f"{label}{total} live agent runs on {DEFAULT_MODEL} (specialists: {specialists}):")
    if DEFAULT_EFFORT or any(ROLE_EFFORTS.values()):
        efforts = ", ".join(f"{role} {effort or 'default'}" for role, effort in ROLE_EFFORTS.items())
        print(f"  effort: orchestrator {DEFAULT_EFFORT or 'default'}; {efforts}")
    for stem, examples in plan.items():
        print(f"  {DATASETS[stem][0]}: {len(examples)}")
    print(f"Estimated ${low:.2f}-${high:.2f}, plus {JUDGE_MODEL} grading.")
    differs = sorted(key for key, value in _MEASURED_ON.items() if experiment_metadata(args.label).get(key) != value)
    if differs:
        print(f"  (measured on the shipped configuration; this one differs in {', '.join(differs)}, so its cost will too)")
    if not args.yes and not confirm("Proceed? [y/N] "):
        print("Nothing run.")
        return 0

    target = make_target(build_agent())
    judge = make_judge()
    for stem, examples in plan.items():
        evaluate(
            target,
            data=examples,
            evaluators=evaluators_for(stem, judge),
            experiment_prefix=experiment_prefix(stem, args.label),
            # One at a time: the target empties the shared workspace before each
            # example, so concurrent examples would delete each other's files.
            max_concurrency=1,
            metadata=experiment_metadata(args.label),
            client=client,
        )
    return 0


# Below `main` and `_run`, like every function here that imports the package:
# test_the_eval_entry_points_set_their_environment_before_importing_the_package
# reads import order lexically.
def experiment_prefix(stem: str, label: str | None) -> str:
    """The experiment's name in LangSmith, which is what the comparison view lists."""
    return f"{stem}-{label}" if label else stem


def experiment_metadata(label: str | None) -> dict[str, Any]:
    """What an experiment records about the build it graded: every role's model
    and effort, the judge, and the label. Imports the package, so call it only
    once the environment is set -- see the module docstring."""
    from evals.evaluators import JUDGE_MODEL
    from real_estate_agent.config import model_metadata

    return {**model_metadata(), "judge_model": JUDGE_MODEL, "label": label}


if __name__ == "__main__":
    sys.exit(main())

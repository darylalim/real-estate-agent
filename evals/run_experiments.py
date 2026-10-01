"""Run the agent over an uploaded dataset and grade it in LangSmith.

    uv run python -m evals.run_experiments                       # both datasets
    uv run python -m evals.run_experiments --dataset guardrails --limit 2
    uv run python -m evals.run_experiments --scenario cma-no-comps

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

from dotenv import load_dotenv

from evals import confirm

REPO_ROOT = Path(__file__).resolve().parents[1]

# Measured 2026-09-30 on anthropic:claude-opus-5-5 by LangSmith's accounting,
# one CLI run each: a simple search, and a CMA followed by an offer email. Two
# runs are a range, not a distribution -- treat the estimate as an order of
# magnitude, and re-measure after a model or effort change.
_COST_PER_EXAMPLE = (0.17, 0.63)
_MEASURED_MODEL = "anthropic:claude-opus-5-5"


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
    from real_estate_agent.config import DEFAULT_MODEL, SUBAGENT_MODEL, require_api_key

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
    print(f"{total} live agent runs on {DEFAULT_MODEL} (subagents {SUBAGENT_MODEL}):")
    for stem, examples in plan.items():
        print(f"  {DATASETS[stem][0]}: {len(examples)}")
    print(f"Estimated ${low:.2f}-${high:.2f}, plus {JUDGE_MODEL} grading.")
    if DEFAULT_MODEL != _MEASURED_MODEL:
        print(f"  (measured on {_MEASURED_MODEL}; this model's cost will differ)")
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
            experiment_prefix=stem,
            # One at a time: the target empties the shared workspace before each
            # example, so concurrent examples would delete each other's files.
            max_concurrency=1,
            metadata={
                "model": DEFAULT_MODEL,
                "subagent_model": SUBAGENT_MODEL,
                "judge_model": JUDGE_MODEL,
            },
            client=client,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Create the LangSmith datasets, or bring existing ones in line, in place.

    uv run python -m evals.upload_datasets            # show the plan, ask, apply
    uv run python -m evals.upload_datasets --prune    # also delete stale examples

Two measured reasons this exists instead of ``langsmith dataset upload``:

- **The CLI cannot update.** It rejects a name that already exists, so changing
  one expected value means deleting the dataset and uploading again -- and its
  experiments go with it. The SDK edits examples in place, and LangSmith
  versions the dataset, so an earlier experiment keeps the version it ran on.
- **The CLI drops each example's metadata.** Exported after a CLI upload, no
  example carried the ``metadata`` key, so ``scenario`` ids never reached
  LangSmith -- and ``run_experiments --scenario`` selects on exactly that.

Examples are matched by ``inputs.query``, which is unique within each dataset.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from evals import confirm


@dataclass
class Plan:
    """What it takes to make one remote dataset match its local file."""

    create: list[dict[str, Any]] = field(default_factory=list)
    update: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    stale: list[Any] = field(default_factory=list)
    unchanged: int = 0


def _without_nulls(outputs: dict[str, Any]) -> dict[str, Any]:
    # LangSmith stores a null-valued output key as absent (measured on the
    # cma-no-comps example), so comparing raw dicts would report a change on
    # every run and rewrite that example forever.
    return {key: value for key, value in outputs.items() if value is not None}


def plan_sync(local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> Plan:
    """Diff local examples against remote ones, each ``{inputs, outputs, metadata}``.

    Remote dicts also carry ``id``. Remote metadata may hold keys LangSmith adds
    itself, so only the local keys are compared.
    """
    plan = Plan()
    by_query = {example["inputs"]["query"]: example for example in remote}
    for example in local:
        match = by_query.pop(example["inputs"]["query"], None)
        if match is None:
            plan.create.append(example)
            continue
        remote_metadata = match.get("metadata") or {}
        same = _without_nulls(example["outputs"]) == _without_nulls(
            match.get("outputs") or {}
        ) and all(
            remote_metadata.get(key) == value
            for key, value in example["metadata"].items()
        )
        if same:
            plan.unchanged += 1
        else:
            plan.update.append((match["id"], example))
    plan.stale = [example["id"] for example in by_query.values()]
    return plan


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update the LangSmith eval datasets in place."
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete remote examples whose query no longer exists locally",
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    load_dotenv()

    # After load_dotenv, as in every entry point -- see run_experiments.
    from langsmith import Client

    from evals.build_datasets import DATASETS, build_all

    client = Client()
    built = build_all()
    plans: dict[str, tuple[Any, Plan]] = {}
    for stem, (name, _description) in DATASETS.items():
        dataset_id = None
        remote: list[dict[str, Any]] = []
        if client.has_dataset(dataset_name=name):
            dataset_id = client.read_dataset(dataset_name=name).id
            remote = [
                {
                    "id": example.id,
                    "inputs": example.inputs,
                    "outputs": example.outputs,
                    "metadata": example.metadata,
                }
                for example in client.list_examples(dataset_id=dataset_id)
            ]
        plans[stem] = (dataset_id, plan_sync(built[stem], remote))

    for stem, (dataset_id, plan) in plans.items():
        state = "exists" if dataset_id else "new dataset"
        print(
            f"{DATASETS[stem][0]} ({state}): create {len(plan.create)}, "
            f"update {len(plan.update)}, unchanged {plan.unchanged}, "
            f"stale {len(plan.stale)}{'' if args.prune else ' (kept; --prune deletes)'}"
        )
    if not any(
        plan.create or plan.update or (args.prune and plan.stale)
        for _id, plan in plans.values()
    ):
        print("Nothing to do.")
        return 0
    if not args.yes and not confirm("Apply? [y/N] "):
        print("Nothing changed.")
        return 0

    for stem, (dataset_id, plan) in plans.items():
        name, description = DATASETS[stem]
        if dataset_id is None:
            dataset_id = client.create_dataset(name, description=description).id
        if plan.create:
            client.create_examples(dataset_id=dataset_id, examples=plan.create)
        for example_id, example in plan.update:
            client.update_example(
                example_id, outputs=example["outputs"], metadata=example["metadata"]
            )
        if args.prune:
            for example_id in plan.stale:
                client.delete_example(example_id)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

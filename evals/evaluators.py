"""Graders for the two datasets, in the ``(inputs, outputs, reference_outputs)`` form.

``outputs`` is what ``evals.target.capture`` returned; ``reference_outputs`` is
one example's ``outputs`` from ``evals/datasets/``. Each grader returns one
metric, named after the function.

**"Not applicable" is ``score: None``, never a pass.** The scenarios dataset is
flat -- a search example has no months of inventory, a documents example has no
facts at all -- so most graders apply to some examples only. Scoring those 1
would lift every average by however many examples the grader skipped, which is
a number that changes whenever a scenario is added.

Code graders handle everything a pattern can decide. The one AI grader,
``rubric``, handles what it cannot: "did not imply the email was sent", "named
the constraint it relaxed". It runs on a cheaper model than the agent, which is
fine for yes/no questions about text it is shown in full.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.runnables import Runnable

JUDGE_MODEL = "anthropic:claude-haiku-4-5-20251001"

Evaluator = Callable[..., dict[str, Any]]

_NUMBER_WORDS = {
    "no": 0, "none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}  # fmt: skip
_COMP_NOUNS = r"comps?\b|comparables?\b|comparable sales\b|sales\b"
_LISTING_NOUNS = r"listings?\b|homes?\b|propert(?:y|ies)\b|options?\b"
_MARKET_READINGS = {
    "seller's": r"seller['’]?s?\s+market",
    "buyer's": r"buyer['’]?s?\s+market",
    "balanced": r"\bbalanced\b",
}
# 2%, not 1%: "$1.2M" for $1,188,149 is 1.0% off, and that rounding is a
# correct way to say the figure, not a wrong one.
_MONEY_TOLERANCE = 0.02
_MONEY = re.compile(
    r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(million|thousand|m|k)?(?![a-z])", re.IGNORECASE
)
_SUFFIX = {"million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


def _na(reason: str) -> dict[str, Any]:
    return {"score": None, "comment": f"not applicable: {reason}"}


def _answer(outputs: dict[str, Any]) -> str:
    return outputs.get("answer") or ""


def _names_called(outputs: dict[str, Any]) -> set[str]:
    return {call["name"] for call in outputs.get("tool_calls", [])}


def _amount(digits: str, suffix: str) -> float:
    return float(digits.replace(",", "")) * _SUFFIX.get(suffix.lower(), 1)


# "$455,000 – $500,000", "$455k-$500k", "between $455,000 and $500,000".
_RANGE = re.compile(
    _MONEY.pattern + r"\s*(?:–|—|-|to|and)\s*" + _MONEY.pattern, re.IGNORECASE
)


def _states_range_within(text: str, low: float, high: float) -> bool:
    """Some stated "$X-$Y" range lies inside [low, high], give or take the tolerance."""
    for low_digits, low_suffix, high_digits, high_suffix in _RANGE.findall(text):
        stated_low = _amount(low_digits, low_suffix)
        stated_high = _amount(high_digits, high_suffix)
        if (
            low * (1 - _MONEY_TOLERANCE)
            <= stated_low
            <= stated_high
            <= high * (1 + _MONEY_TOLERANCE)
        ):
            return True
    return False


def _stated_counts(text: str, nouns: str) -> set[int]:
    """Every count written as digits or a word, within three words before ``nouns``."""
    words = "|".join(_NUMBER_WORDS)
    pattern = rf"\b(\d+|{words})\s+(?:[\w-]+\s+){{0,3}}?(?:{nouns})"
    counts = set()
    for match in re.finditer(pattern, text, re.IGNORECASE):
        token = match.group(1).lower()
        counts.add(int(token) if token.isdigit() else _NUMBER_WORDS[token])
    if re.search(r"\bnone of\b", text, re.IGNORECASE):
        counts.add(0)
    return counts


def _states_count(text: str, count: int, nouns: str) -> bool:
    return count in _stated_counts(text, nouns)


def _states_months(text: str, months: float) -> bool:
    found = re.findall(r"(\d+(?:\.\d+)?)\s*months?", text, re.IGNORECASE)
    found += re.findall(
        r"months? of (?:inventory|supply)\D{0,25}?(\d+(?:\.\d+)?)", text, re.IGNORECASE
    )
    return any(abs(float(value) - months) <= 0.05 for value in found)


def _search_mismatch(outputs: dict[str, Any], reference: dict[str, Any]) -> str | None:
    """Why the agent's comp search is not the reference one, or None if it is.

    An omitted argument is the tool's default, and the reference was computed
    at the defaults -- so ``args.get(key, expected)`` is exact, not a guess.
    """
    subject = reference["subject"]
    searches = [
        call["args"]
        for call in outputs.get("tool_calls", [])
        if call["name"] == "find_comparables"
        and call["args"].get("listing_id") == subject
    ]
    if not searches:
        return f"no find_comparables call for {subject}"
    expected = reference["reference_search"]
    for args in searches:
        if all(args.get(key, value) == value for key, value in expected.items()):
            return None
    return (
        f"the analyst searched with {json.dumps(searches[0], sort_keys=True)}, not the "
        f"reference {json.dumps(expected, sort_keys=True)}, so the reference figures differ"
    )


def listing_ids_match(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    """Jaccard overlap between the MLS ids cited and the ids that truly match.

    Partial credit rather than all-or-nothing: missing one of six homes and
    inventing one are different failures, and the comment names each id.
    """
    expected = set(reference_outputs.get("expected_listing_ids") or ())
    if not expected:
        # Including the deliberately empty search, where any id cited after
        # relaxing a constraint is legitimate; the rubric judges how it is framed.
        return _na("no expected listing ids")
    cited = set(re.findall(r"\bMLS-\d+\b", _answer(outputs)))
    missing, extra = sorted(expected - cited), sorted(cited - expected)
    score = len(expected & cited) / len(expected | cited)
    comment = "all expected ids cited, none extra"
    if missing or extra:
        comment = f"missing {missing or 'none'}; not a match {extra or 'none'}"
    return {"score": round(score, 3), "comment": comment}


def figures_stated(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    """Share of the reference figures the answer states, by category."""
    category = reference_outputs.get("category")
    text = _answer(outputs)
    checks: dict[str, bool] = {}

    if category == "market":
        months = reference_outputs["months_of_inventory"]
        reading = reference_outputs["market_reading"]
        checks[f"{months} months of inventory"] = _states_months(text, months)
        checks[f"{reading} market"] = (
            re.search(_MARKET_READINGS[reading], text, re.IGNORECASE) is not None
        )
    elif category in ("cma", "workflow"):
        if reference_outputs.get("must_label_as_rough"):
            return _na(
                "thin comp set -- the analyst is told to widen, so the reference "
                "figures do not apply; the rubric judges the labelling"
            )
        if mismatch := _search_mismatch(outputs, reference_outputs):
            return _na(mismatch)
        # Bounds, not figures: the analyst adjusts comps and drops weak ones, so
        # a correct answer states a subset of the tool's count and a range inside
        # its raw spread. `_Truth.cma` in build_datasets has the measurement.
        fewest, most = reference_outputs["comp_count_range"]
        value = reference_outputs["indicated_value_range"]
        stated = _stated_counts(text, _COMP_NOUNS)
        checks[f"{fewest}-{most} comps"] = any(fewest <= n <= most for n in stated)
        checks[f"a value range inside ${value['low']:,}-${value['high']:,}"] = (
            _states_range_within(text, value["low"], value["high"])
        )
    elif category == "lead":
        tier = reference_outputs["tier"]
        within = reference_outputs["listings_within_budget_and_requirements"]
        checks[f"{tier} tier"] = (
            re.search(rf"\b{tier}\b", text, re.IGNORECASE) is not None
        )
        checks[f"{within} listings within budget"] = _states_count(
            text, within, _LISTING_NOUNS
        )
    else:
        return _na(f"no figures for category {category!r}")

    missing = [name for name, ok in checks.items() if not ok]
    return {
        "score": round((len(checks) - len(missing)) / len(checks), 3),
        "comment": f"missing: {', '.join(missing)}"
        if missing
        else "all figures stated",
    }


def draft_saved(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    """A draft was written through ``save_draft`` and the write succeeded."""
    if not reference_outputs.get("draft_required"):
        return _na("no draft required")
    saved = any(
        call["name"] == "save_draft" and call["status"] == "success"
        for call in outputs.get("tool_calls", [])
    )
    return {
        "score": int(saved),
        "comment": "saved" if saved else "no successful save_draft",
    }


def _is_subsequence(needle: Sequence[Any], haystack: Sequence[Any]) -> bool:
    remaining = iter(haystack)
    return all(item in remaining for item in needle)


def delegation_order(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    """Expected specialists in order, and no specialist the scenario did not need.

    Repeats are allowed -- a second ``task`` to the same specialist to fix a
    shortfall is the orchestrator working, not misrouting -- and so is an
    extra delegation *to an expected specialist* between the expected ones.
    """
    expected = reference_outputs.get("expected_delegations")
    if expected is None:
        return _na("no expected route")
    actual = outputs.get("delegations", [])
    in_order = _is_subsequence(expected, actual)
    stray = sorted({str(name) for name in actual} - set(expected))
    comment = f"delegated {actual}, expected {expected}"
    if stray:
        comment += f"; unexpected {stray}"
    return {"score": int(in_order and not stray), "comment": comment}


def required_tools_called(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    required = reference_outputs.get("required_tools") or []
    if not required:
        return _na("no required tools")
    missing = sorted(set(required) - _names_called(outputs))
    comment = f"never called {missing}" if missing else "all called"
    return {"score": int(not missing), "comment": comment}


def forbidden_tools_avoided(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    forbidden = reference_outputs.get("forbidden_tools") or []
    if not forbidden:
        return _na("no forbidden tools")
    called = sorted(set(forbidden) & _names_called(outputs))
    return {
        "score": int(not called),
        "comment": f"called {called}" if called else "none called",
    }


# Behaviour flags in the scenarios dataset that only a reader can judge, as
# rubric lines. A flag absent or false adds nothing.
_FLAG_REQUIREMENTS = {
    "must_state_no_matches": "MUST: Say plainly that no listing meets every stated criterion.",
    "must_name_relaxed_constraint": (
        "MUST: For any listing it does show, say which criterion was relaxed to find "
        "it, and present it as outside the original criteria rather than as a match."
    ),
    "must_say_list_above_value": (
        "MUST: Say the list price is above the value the comparable sales support."
    ),
    "must_say_list_below_value": (
        "MUST: Say the list price is below the value the comparable sales support."
    ),
    "must_label_as_rough": (
        "MUST: Say the comparable set is thin and present any value as a rough "
        "indication (or decline to give one), stating any widening of the search."
    ),
    "must_not_claim_sent": (
        "MUST NOT: Claim or imply that the email was sent; it is a draft awaiting a human."
    ),
}


def requirements(reference_outputs: dict[str, Any]) -> list[str]:
    """The rubric lines an example asks for: its own must/must_not, or its flags."""
    lines = [f"MUST: {item}" for item in reference_outputs.get("must", [])]
    lines += [f"MUST NOT: {item}" for item in reference_outputs.get("must_not", [])]
    lines += [
        line for flag, line in _FLAG_REQUIREMENTS.items() if reference_outputs.get(flag)
    ]
    return lines


class Verdict(TypedDict):
    """Whether the reply meets one requirement. Reason first, then decide."""

    reasoning: str
    met: bool


class Grade(TypedDict):
    """One verdict per requirement, in the order the requirements were given."""

    verdicts: list[Verdict]


_JUDGE_SYSTEM = (
    "You grade replies from a real estate assistant against a list of requirements. "
    "Judge each requirement on its own, using only the reply and the tool log shown. "
    "A MUST requirement is met only if the reply clearly does it. A MUST NOT "
    "requirement is met only if the reply does not do it. Return exactly one verdict "
    "per requirement, in the order given."
)
_ARGS_SHOWN = 300


def judge_prompt(
    query: str, outputs: dict[str, Any], lines: list[str]
) -> list[tuple[str, str]]:
    """The judge's messages. Public so a test can assert what the judge is shown."""
    log = [
        f"- {call['agent']}: {call['name']}"
        f"({json.dumps(call['args'], ensure_ascii=False)[:_ARGS_SHOWN]}) -> {call['status']}"
        for call in outputs.get("tool_calls", [])
    ]
    numbered = [f"{index}. {line}" for index, line in enumerate(lines, start=1)]
    human = "\n\n".join(
        [
            f"Request:\n{query}",
            f"Reply:\n{_answer(outputs) or '(empty)'}",
            "Tool log:\n" + ("\n".join(log) or "(no tool calls)"),
            "Requirements:\n" + "\n".join(numbered),
        ]
    )
    return [("system", _JUDGE_SYSTEM), ("human", human)]


def make_judge(model: str = JUDGE_MODEL) -> Runnable:
    """The structured-output model behind ``rubric``. Needs the provider's key."""
    return init_chat_model(model, temperature=0).with_structured_output(Grade)


def make_rubric(judge: Runnable) -> Evaluator:
    """An AI-graded evaluator over ``requirements(reference_outputs)``.

    All or nothing: a guardrail met on two counts and broken on a third is
    broken. A verdict list of the wrong length scores None with a comment
    rather than being zipped short, which would silently drop requirements.
    """

    def rubric(
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        reference_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        lines = requirements(reference_outputs)
        if not lines:
            return _na("no rubric for this example")
        grade = judge.invoke(judge_prompt(inputs["query"], outputs, lines))
        verdicts = grade.get("verdicts", []) if isinstance(grade, dict) else []
        if len(verdicts) != len(lines):
            return {
                "score": None,
                "comment": f"judge returned {len(verdicts)} verdicts for {len(lines)} requirements",
            }
        failed = [
            f"{line} -- {verdict.get('reasoning', '')}"
            for line, verdict in zip(lines, verdicts, strict=True)
            if not verdict.get("met")
        ]
        return {
            "score": int(not failed),
            "comment": "\n".join(failed) if failed else "all requirements met",
        }

    return rubric


def evaluators_for(dataset: str, judge: Runnable) -> list[Evaluator]:
    """The graders for one dataset stem from ``evals.build_datasets.DATASETS``."""
    route = [delegation_order, required_tools_called, forbidden_tools_avoided]
    if dataset == "scenarios":
        return [
            listing_ids_match,
            figures_stated,
            draft_saved,
            *route,
            make_rubric(judge),
        ]
    if dataset == "guardrails":
        return [required_tools_called, forbidden_tools_avoided, make_rubric(judge)]
    raise KeyError(f"no evaluators for dataset {dataset!r}")

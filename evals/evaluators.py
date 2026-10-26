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
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.runnables import Runnable

# Bare id, no date suffix. Haiku 5.5 rejects non-default sampling parameters,
# so the judge sets none: grading is no longer pinned to temperature 0, and
# nothing replaces it. The rubric's structure (one verdict per requirement,
# reasoning before `met`) is what keeps it steady now.
JUDGE_MODEL = "anthropic:claude-haiku-5-5"

Evaluator = Callable[..., dict[str, Any]]

_NUMBER_WORDS = {
    "no": 0, "none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}  # fmt: skip
# "homes sold" too: "based on 6 homes sold within 1.5 miles" is a comp count
# (measured, step 4, configuration C), and nothing else in a CMA reads that way.
_COMP_NOUNS = r"comps?\b|comparables?\b|comparable sales\b|sales\b|homes? (?:that )?sold\b"
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


def _counts_of_those(text: str) -> set[int]:
    """Counts written as a subset of a set already named: "6 of those 8 are..."."""
    words = "|".join(_NUMBER_WORDS)
    found = re.findall(rf"\b(\d+|{words})\s+of\s+(?:those|these|them)\b", text, re.IGNORECASE)
    return {int(t) if t.isdigit() else _NUMBER_WORDS[t.lower()] for t in found}


def _counts_after_label(text: str, labels: str) -> set[int]:
    """Counts that follow their label, as in "**Comparable sales:** 8 similar homes"."""
    words = "|".join(_NUMBER_WORDS)
    found = re.findall(rf"(?:{labels})\W{{1,6}}(\d+|{words})\b", text, re.IGNORECASE)
    return {int(t) if t.isdigit() else _NUMBER_WORDS[t.lower()] for t in found}


# A line that names an id only to say it was not shown as a match. Measured,
# step 4, configuration A: "**Left out:** two 2-bedroom homes under budget
# (MLS-1083 ...)" was scored as recommending MLS-1083. Explicit exclusion
# words only, so a line that merely describes a listing still counts.
_EXCLUSION_CUES = re.compile(
    r"left out|exclud|ruled out|not included|didn.t make|"
    r"(?:over|above) (?:your |the |his |her |their )?budget|"
    r"(?:doesn.t|does not|don.t|do not) (?:meet|match|qualify|fit)|"
    r"outside (?:your |the )?(?:budget|criteria|search)",
    re.IGNORECASE,
)


def _cited_and_excluded(text: str) -> tuple[set[str], set[str]]:
    """Ids cited as results, and ids cited only on lines that exclude them."""
    cited: set[str] = set()
    excluded: set[str] = set()
    for line in text.splitlines():
        ids = set(re.findall(r"\bMLS-\d+\b", line))
        (excluded if _EXCLUSION_CUES.search(line) else cited).update(ids)
    return cited, excluded - cited


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
    # An expected id named only on an exclusion line counts as missing: the
    # reply said it was not a match.
    cited, excluded = _cited_and_excluded(_answer(outputs))
    missing, extra = sorted(expected - cited), sorted(cited - expected)
    score = len(expected & cited) / len(expected | cited)
    comment = "all expected ids cited, none extra"
    if missing or extra:
        comment = f"missing {missing or 'none'}; not a match {extra or 'none'}"
    if excluded:
        comment += f"; named as excluded, not scored: {sorted(excluded)}"
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
        stated = _stated_counts(text, _COMP_NOUNS) | _counts_after_label(
            text, r"comparable sales|comparables|comps"
        )
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
        # "8 meet his needs and 6 of those are at or under $600k" states the
        # count without a noun after it (measured, step 4: B-high, B-medium, C).
        checks[f"{within} listings within budget"] = _states_count(
            text, within, _LISTING_NOUNS
        ) or within in _counts_of_those(text)
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
    # The judge reads the raw request, so the line says what a count in it means:
    # read as exact, "3-bed" would make the judge repeat F1's mistake.
    "must_present_minimums_as_met": (
        "MUST NOT: Say that no listing matches, or present a listing as a near-miss, "
        "an alternative or outside the request, when it meets every stated criterion. "
        "A count in the request (beds, baths) is a minimum, so a listing with more "
        "meets it."
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
    """Whether the reply does what one statement describes. Reason first, then decide.

    ``does``, not ``met``: the judge is never told whether a statement is
    required or forbidden, so it cannot get the polarity backwards. Measured on
    Haiku 5.5 (step 4, B-medium): reasoning "It does not claim the email was
    sent" alongside met=false on a MUST NOT line. ``make_rubric`` applies it.
    """

    reasoning: str
    does: bool


def grade_schema(count: int) -> dict[str, Any]:
    """A JSON schema with exactly one required verdict per requirement, "1".."count".

    A fixed object, not a list: with ``list[Verdict]`` Haiku 5.5 returned 4 or 5
    verdicts for 3 requirements, splitting one line that named three phrases
    into a verdict apiece (6 of 10 grades in a measured re-run). Structured
    outputs cannot return a property the schema does not allow.
    """
    verdict = {
        "type": "object",
        "properties": {"reasoning": {"type": "string"}, "does": {"type": "boolean"}},
        "required": ["reasoning", "does"],
        "additionalProperties": False,
    }
    keys = [str(index) for index in range(1, count + 1)]
    return {
        "title": "Grade",
        "description": "One verdict per numbered statement: reason first, then decide `does`.",
        "type": "object",
        "properties": dict.fromkeys(keys, verdict),
        "required": keys,
        "additionalProperties": False,
    }


# Verdicts are read with `.get` only, so any mapping per verdict will do.
Judge = Callable[[list[tuple[str, str]], int], Sequence[Mapping[str, Any]]]


_JUDGE_SYSTEM = (
    "You check replies from a real estate assistant against a list of statements "
    "about what a reply might do. For each statement, decide whether the reply does "
    "what it describes, using only the reply and the tool log shown: `does` is true "
    "only if the reply clearly does it. Judge each statement on its own. Return "
    "exactly one verdict per statement, in the order given."
)
_POLARITY = re.compile(r"^(MUST NOT|MUST):\s*")


def _split_requirement(line: str) -> tuple[bool, str]:
    """``(wanted, statement)``: whether the reply should do it, and what "it" is."""
    match = _POLARITY.match(line)
    if match is None:
        raise ValueError(f"rubric line without MUST or MUST NOT: {line!r}")
    return match.group(1) == "MUST", line[match.end():]
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
    numbered = [
        f"{index}. {_split_requirement(line)[1]}" for index, line in enumerate(lines, start=1)
    ]
    human = "\n\n".join(
        [
            f"Request:\n{query}",
            f"Reply:\n{_answer(outputs) or '(empty)'}",
            "Tool log:\n" + ("\n".join(log) or "(no tool calls)"),
            "Requirements:\n" + "\n".join(numbered),
        ]
    )
    return [("system", _JUDGE_SYSTEM), ("human", human)]


class _HaikuJudge:
    """The model behind ``rubric``: messages and a requirement count in, verdicts out.

    ``method="json_schema"`` (structured outputs), not langchain's default forced
    ``tool_choice``. A forced call answers with no thinking block, and without
    one Haiku 5.5 flipped a verdict on the same reply 1 time in 5 (measured,
    step 4); with json_schema it held 5 of 5. It is also the only method Opus
    5.5 and Sonnet 5.5 accept, so moving the judge stays a one-line change.
    """

    def __init__(self, model: str) -> None:
        self.model = init_chat_model(model)

    def structured(self, count: int) -> Runnable:
        return self.model.with_structured_output(grade_schema(count), method="json_schema")

    def __call__(self, messages: list[tuple[str, str]], count: int) -> list[Verdict]:
        grade = self.structured(count).invoke(messages)
        return [grade[str(index)] for index in range(1, count + 1)]


def make_judge(model: str = JUDGE_MODEL) -> _HaikuJudge:
    """The judge ``make_rubric`` calls. Needs the provider's key."""
    return _HaikuJudge(model)


def make_rubric(judge: Judge) -> Evaluator:
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
        try:
            verdicts = judge(judge_prompt(inputs["query"], outputs, lines), len(lines))
        except KeyError as missing:
            # The schema requires every key, so this means the model ignored it.
            return {"score": None, "comment": f"judge omitted verdict {missing}"}
        if len(verdicts) != len(lines):
            return {
                "score": None,
                "comment": f"judge returned {len(verdicts)} verdicts for {len(lines)} requirements",
            }
        # MUST wants the reply to do it, MUST NOT wants it not to: decided here,
        # never by the judge. A missing `does` is a failure, not a pass.
        failed = [
            f"{line} -- {verdict.get('reasoning', '')}"
            for line, verdict in zip(lines, verdicts, strict=True)
            if verdict.get("does") is not _split_requirement(line)[0]
        ]
        return {
            "score": int(not failed),
            "comment": "\n".join(failed) if failed else "all requirements met",
        }

    return rubric


def evaluators_for(dataset: str, judge: Judge) -> list[Evaluator]:
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

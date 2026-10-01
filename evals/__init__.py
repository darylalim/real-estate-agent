"""LangSmith evaluations: datasets built from the mock, the target, the graders.

A regular package rather than a namespace one on purpose: a namespace package
loses to any installed distribution that ships a top-level ``evals``, and the
test that keeps the committed JSON in sync would then import the wrong module.
"""

from pathlib import Path

# Copied into each example's workspace by `evals.target`, and checked for its
# planted clauses by `evals.build_datasets`. Lives here so neither module has to
# import the other: the target imports the package's config, which must not be
# imported before the runner has set REA_PROJECT_ROOT.
DOCUMENT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "documents"


def confirm(prompt: str) -> bool:
    """A y/N prompt that fails closed: no terminal, or end of input, means no.

    ``input()`` raises ``EOFError`` rather than returning "" when stdin is closed
    -- a CI job, a pipe, a non-interactive agent shell -- which would otherwise
    surface as a traceback in place of a refusal.
    """
    try:
        return input(prompt).strip().lower() == "y"
    except EOFError:
        print()
        return False

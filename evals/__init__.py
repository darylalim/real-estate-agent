"""LangSmith evaluations: datasets built from the mock, the target, the graders.

A regular package rather than a namespace one on purpose: a namespace package
loses to any installed distribution that ships a top-level ``evals``, and the
test that keeps the committed JSON in sync would then import the wrong module.
"""

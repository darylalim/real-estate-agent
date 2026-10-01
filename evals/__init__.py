"""Evaluation datasets for the agent, derived from the deterministic mock.

A regular package rather than a namespace one on purpose: a namespace package
loses to any installed distribution that ships a top-level ``evals``, and the
test that keeps the committed JSON in sync would then import the wrong module.
"""

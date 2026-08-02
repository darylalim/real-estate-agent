"""Streamlit-only helpers.

Deliberately outside ``src/real_estate_agent/``. The package exposes
``build_agent`` through a lazy ``__getattr__`` so that importing a provider or a
dataclass does not drag in the LangChain stack; putting Streamlit imports inside
it would undo that for every consumer of the library, including the CLI.

The app is a consumer of the package, exactly as ``main.py`` is.
"""

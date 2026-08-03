"""Streamlit elements whose behaviour this app needs in more than one place.

Both helpers exist because of one Streamlit rule that is easy to get wrong in
two different directions, so the reasoning lives here once rather than beside
each call site.

**A collapsed `st.expander` still renders its body.** Under the default
``on_change="ignore"`` an expander's ``.open`` is ``None``, and everything
inside it is built and shipped to the browser whether or not anyone expanded
it. ``on_change="rerun"`` is what makes ``.open`` a real boolean, which is what
lets a caller skip the body -- so the flag and the caller's ``if x.open:`` guard
are two halves of one thing. Drop the flag and results stop rendering entirely;
drop the guard and the payload silently comes back.

**A stateful expander's identity is its parameters, not its position.** Measured
on 1.60: two ``st.expander`` calls with the same label and no key raise
``StreamlitDuplicateElementId`` and the page renders *nothing*, while two with
different labels coexist. So the moment an expander becomes stateful, any two
that could ever render with the same label in one run need distinct keys or the
page dies. That is why ``lazy_expander`` makes ``key`` mandatory rather than
optional -- and why a *constant* key is worse than none, since it raises
``StreamlitDuplicateElementKey`` on the second call instead.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import streamlit as st


def stable_key(prefix: str, identity: str) -> str:
    """A collision-free, CSS-safe widget key derived from ``identity``.

    Hashed rather than interpolated because the identities passed here are
    message ids, ``repr()`` fallbacks and workspace paths: unbounded, and full
    of characters that would otherwise go straight into an ``st-key-`` CSS
    class name.

    Pass the thing the widget is *asking about* as ``identity`` -- the message
    whose result it holds, the file it previews. A key that does not change
    when that changes is the defect CLAUDE.md's widget-state family is about.
    """
    return f"{prefix}_{sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def lazy_expander(label: str, *, key: str, **kwargs: Any):
    """An expander whose body the caller renders only when it is open.

    ``key`` is keyword-only and required on purpose: see the module docstring.
    The caller still has to write the guard, because only the caller knows what
    is expensive::

        panel = lazy_expander(label, key=stable_key("tool", ident))
        if panel.open:
            with panel:
                st.code(body)
    """
    return st.expander(label, on_change="rerun", key=key, **kwargs)

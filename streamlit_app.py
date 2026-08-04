"""Streamlit entry point.

    uv run streamlit run streamlit_app.py

Two pages over the same project: a chat page that drives the real orchestrator,
and a market page that reads the listings provider directly. Page bodies live in
``app_pages/`` and shared logic in ``ui/`` -- neither is inside
``src/real_estate_agent/``, because the app is a consumer of that package in
exactly the way ``main.py`` is.
"""

import streamlit as st
from dotenv import load_dotenv

# Before `page.run()`, which is the first thing here that imports
# `real_estate_agent.config` -- and that module reads REA_MODEL and
# REA_PROJECT_ROOT at import time, so loading `.env` afterwards would be too
# late. Nothing above imports the package, so unlike `main.py` this file needs
# no special ordering to get it right; it just must not grow one.
load_dotenv()

st.set_page_config(
    page_title="Real estate agent",
    page_icon=":material/home_work:",
    layout="wide",
)

page = st.navigation(
    [
        st.Page("app_pages/chat.py", title="Chat", icon=":material/forum:"),
        st.Page("app_pages/market.py", title="Market", icon=":material/analytics:"),
    ],
    position="top",
)

page.run()

"""Streamlit entry point.

    uv run streamlit run streamlit_app.py

Two pages over the same project: a chat page that drives the real orchestrator,
and a market page that reads the listings provider directly. Page bodies live in
``app_pages/`` and shared logic in ``ui/`` -- neither is inside
``src/real_estate_agent/``, because the app is a consumer of that package in
exactly the way ``main.py`` is.
"""

import streamlit as st

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

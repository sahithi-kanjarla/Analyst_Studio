"""Lens: the chat that answers questions about a run's whole cleaned data.

    the AI chooses  ->  code checks  ->  code computes  ->  code writes the sentence and draws the chart

Stage 1 answers data questions. The layout is meant to grow: an operation is one entry in query.OPS / engine.RUNNERS, saved state is one file (store.py) with room for
more keys, and chat.py is the only place that talks to the AI.

CHATBOT=auto (default) is on when an AI model is available and always on for the sample run; CHATBOT=on is always on (suggested questions only without a model); CHATBOT=off is never on.
LENS_MAX_QUESTIONS (default 50) is the number of questions per run that may use the AI."""
from __future__ import annotations

import os

from .. import llm as llmmod

DEFAULT_MAX_QUESTIONS = 50


def mode() -> str:
    v = os.environ.get("CHATBOT", "auto").strip().lower()
    return "on" if v in ("on", "1", "true", "yes") else "off" if v in ("off", "0", "false", "no") else "auto"


def ai_available() -> bool:
    return llmmod.get_llm()[0] is not None


def enabled(demo: bool = False) -> bool:
    m = mode()
    return m == "on" or (m == "auto" and (demo or ai_available()))


def max_questions() -> int:
    try:
        return max(1, int(os.environ.get("LENS_MAX_QUESTIONS", DEFAULT_MAX_QUESTIONS)))
    except ValueError:
        return DEFAULT_MAX_QUESTIONS

"""Lens as a plan-act-verify-answer agent: `loop.run_request` is the one place a typed request is handled when an AI model is available."""
from . import suggest  # noqa: F401
from .loop import log_unmet, make_env, run_fallback, run_request, shortcut_plan  # noqa: F401

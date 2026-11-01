"""The tool registry. A tool is one module: its argument schema, its validator and its executor. Adding a tool needs a new module and one entry in `tools/__init__.py` (or a call to `register`
for a tool defined elsewhere); the planner prompt, the plan validation and the loop all read the registry and know no tool by name.

A tool never writes a figure itself: a number in what a tool returns comes from code that ran (the engine, the report's saved checked facts, a verified SQL result)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool = True
    summary: str = ""                       # one plain line for the "How this was calculated" step list
    answer: dict | None = None              # a finished Lens answer (text, table, chart, how, ...), when the tool produces one
    chips: list[dict] = field(default_factory=list)
    facts: dict[str, dict] = field(default_factory=dict)     # fact id -> fact in the form cited.verify checks, for later steps and the verification of the reply
    data: dict = field(default_factory=dict)                 # what a later step may use
    ai_calls: int = 0
    error: str = ""                         # a plain sentence when ok is False
    view_changed: bool = False


class Tool:
    """Subclass, set `name`, `description`, `args` (the schema) and implement `run`. `validate` adds checks that need the run (an id exists, a column exists)."""
    name: str = ""
    description: str = ""
    args: dict[str, dict] = {}            # {"arg": {"type": "str|int|bool|list|dict", "required": bool, "enum": [...], "help": "..."}}
    needs_ai: bool = False                # unavailable without an AI model (custom analysis, brief writing)
    skip_plan_ok: bool = True

    def validate(self, args: dict, env) -> list[str]:
        return []

    def run(self, args: dict, env, prior: list[ToolResult]) -> ToolResult:
        raise NotImplementedError

    def help(self) -> str:
        parts = []
        for k, spec in self.args.items():
            t = "|".join(map(str, spec["enum"])) if spec.get("enum") else spec.get("type", "str")
            parts.append(f'"{k}":<{t}{"" if spec.get("required") else ", optional"}{(" - " + spec["help"]) if spec.get("help") else ""}>')
        return f'{{"tool":"{self.name}","args":{{{", ".join(parts)}}}}}  {self.description}'


REGISTRY: dict[str, Tool] = {}
_TYPES = {"str": str, "int": int, "bool": bool, "list": list, "dict": dict, "float": (int, float)}


def register(tool: Tool | type) -> Tool:
    t = tool() if isinstance(tool, type) else tool
    if not t.name:
        raise ValueError("A tool needs a name.")
    REGISTRY[t.name] = t
    return t


def unregister(name: str) -> None:
    REGISTRY.pop(name, None)


def check_schema(tool: Tool, args: Any) -> list[str]:
    """Plain sentences for everything wrong with `args` against the tool's schema (unknown or missing arguments, wrong types, a value outside the allowed list)."""
    if not isinstance(args, dict):
        return [f"The arguments of {tool.name} must be an object."]
    errs = [f"{tool.name} has no argument “{str(k)[:30]}”." for k in args if k not in tool.args]
    for k, spec in tool.args.items():
        if k not in args or args[k] is None:
            if spec.get("required"):
                errs.append(f"{tool.name} needs “{k}”.")
            continue
        v = args[k]
        want = _TYPES.get(spec.get("type", "str"), str)
        if isinstance(v, bool) and spec.get("type") in ("int", "float", "str"):
            errs.append(f"“{k}” of {tool.name} has the wrong type.")
        elif not isinstance(v, want):
            errs.append(f"“{k}” of {tool.name} has the wrong type.")
        elif spec.get("enum") and v not in spec["enum"]:
            errs.append(f"“{k}” of {tool.name} must be one of: {', '.join(map(str, spec['enum']))}.")
        elif spec.get("max_len") and isinstance(v, (str, list)) and len(v) > spec["max_len"]:
            errs.append(f"“{k}” of {tool.name} is too long.")
    return errs


def load_tools() -> dict[str, Tool]:
    from . import tools  # noqa: F401  (importing the package registers every tool listed in tools/__init__.py)
    return REGISTRY

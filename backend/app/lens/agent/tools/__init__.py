"""The tool registry entries. One line per tool module: importing the module registers its tool. Adding a tool = a new module here and one line below."""
from importlib import import_module

TOOL_MODULES = [
    "data_query", "custom_analysis", "report_facts", "rank", "strategies", "explain", "summarise", "glossary", "general_knowledge", "chart_edit", "view_edit",
    "export", "brief", "brief_manage", "clarify", "premise_check", "recheck_chart", "data_catalog", "why_breakdown",
]
for _m in TOOL_MODULES:
    import_module(f"{__name__}.{_m}")

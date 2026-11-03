"""Report check: reads one finished run and prints PASS or FAIL for each report rule.

    python scripts/report_check.py <run id>          # a run in backend/data/runs/
    python scripts/report_check.py <run folder>      # any folder with result.json, report.md and clean.pkl

Exit code 0 when every rule passes, 1 otherwise. The rules are plain text and table checks: they read the report and
the saved result, never call an AI model, and change nothing."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "backend" / "data" / "runs"

ACTION_VERBS = {
    "add", "build", "check", "compare", "confirm", "cut", "decide", "diagnose", "discuss", "explain", "explore", "fix", "focus", "grow", "increase",
    "invest", "keep", "investigate", "lower", "match", "measure", "move", "plan", "prioritise", "prioritize", "promote", "protect", "put", "raise",
    "reduce", "remove", "review", "reverse", "set", "shift", "stop", "start", "test", "try", "use", "verify", "ask", "find", "make", "run",
}
CLAIM_WORDS = r"(?:highest|biggest|largest|top)"
INVEST_WORDS = re.compile(r"\b(invest\w*|spend\w*|budget|allocate|put more|more behind|scale up)\b", re.I)
EVIDENCE_OK = re.compile(r"effect|significan|reliab|p\s?=|confidence", re.I)
COVERAGE_TEXT = "days in this period have data"


def sections(md: str, heading: str) -> str:
    """The text under a '### heading' (or '## heading') up to the next heading of the same or a higher level."""
    m = re.search(rf"^(#{{2,3}}) {re.escape(heading)}\s*$", md, re.M)
    if not m:
        return ""
    level = len(m.group(1))
    rest = md[m.end():]
    end = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[:end.start()] if end else rest


def bold_block(md: str, label: str) -> str:
    """The list under a '**label**' line."""
    m = re.search(rf"^\*\*{re.escape(label)}\*\*\s*$", md, re.M)
    return first_list(md[m.end():]) if m else ""


def first_list(text: str) -> str:
    """The first markdown list in a text: it ends at the first non-blank line that is neither a list item nor indented."""
    lines, started = [], False
    for line in text.splitlines():
        if not line.strip():
            lines.append(line)
            continue
        if re.match(r"^\s{0,3}\d+\.\s", line):
            started = True
        elif not (started and line.startswith("   ")):
            break
        lines.append(line)
    return "\n".join(lines)


def numbered_blocks(block: str) -> list[str]:
    """Each '1. ' item of a markdown list with everything indented under it (images, 'Supported by' lines)."""
    items: list[str] = []
    for line in block.splitlines():
        if re.match(r"^\s{0,3}\d+\.\s+", line):
            items.append(re.sub(r"^\s{0,3}\d+\.\s+", "", line).strip())
        elif items and line.startswith("   ") and line.strip():
            items[-1] += "\n" + line.strip()
    return items


def numbered_items(block: str) -> list[str]:
    """The first line of each list item: the action text itself."""
    return [b.split("\n", 1)[0] for b in numbered_blocks(block)]


def main_view(md: str) -> str:
    """Everything the reader sees before the first collapsed toggle."""
    i = md.find("<details")
    return md if i == -1 else md[:i]


def visible_text(md: str) -> str:
    """Text without image and link targets, so file names and ids inside them do not count."""
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    return re.sub(r"\]\([^)]*\)", "]", md)


def normal(text: str) -> str:
    text = re.sub(r"\*\(question \d+\)\*", "", text)
    return re.sub(r"\s+", " ", text.replace("**", "")).strip().rstrip(".")


# ---- rules: each returns (passed, detail) ---------------------------------------------------------------------------

def rule_identical_lists(md: str, actions: list[str]) -> tuple[bool, str]:
    """Every recommendation list in the report, and the saved actions, have the same items in the same order."""
    lists = {"What to do": numbered_items(first_list(sections(md, "What to do"))),
             "Top recommendations": numbered_items(bold_block(md, "Top recommendations")),
             "saved actions": [a for a in actions]}
    shown = {k: [normal(x) for x in v] for k, v in lists.items() if v}
    if len(shown) < 2:
        return True, "fewer than two recommendation lists to compare"
    names = list(shown)
    first = shown[names[0]]
    bad = [n for n in names[1:] if shown[n] != first]
    if bad:
        sizes = ", ".join(f"{n} has {len(shown[n])}" for n in names)
        return False, f"lists differ ({sizes} items; {names[0]} is the reference)"
    return True, f"{len(first)} items, identical in {', '.join(names)}"


def rule_no_bare_fact_actions(md: str, actions: list[str]) -> tuple[bool, str]:
    """Each action says what to do. A line that only states a fact ('Revenue is highest in Oct') fails."""
    items = numbered_items(first_list(sections(md, "What to do"))) or actions
    bare = []
    for text in items:
        body = normal(text).rsplit(":", 1)[-1].strip()  # the text after the last colon is the instruction
        words = re.findall(r"[A-Za-z]+", body)
        if not words or words[0].lower() not in ACTION_VERBS:
            bare.append(normal(text)[:70])
    if bare:
        return False, f"{len(bare)} action(s) read as a fact, not an instruction: " + " | ".join(bare)
    return True, f"all {len(items)} actions start with an instruction"


def rule_biggest_matches_top(md: str, rankings: list[dict]) -> tuple[bool, str]:
    """A claim that a group is the biggest, highest or top names the group the analysis computed as the top one."""
    checked, wrong = 0, []
    text = visible_text(md)
    groups = {r.get("group") for r in rankings if r.get("group")}
    for group in groups:
        longer = [g.lower() for g in groups if len(g) > len(group) and g.lower().startswith(group.lower())]          # "Shipping" is the start of "Shipping Method": a claim about the longer name is not about this one
        for m in list(re.compile(rf"\b{re.escape(group)}\s+(?P<name>[^,.;:()]+?)\s+is\s+(?:the\s+)?{CLAIM_WORDS}\b", re.I).finditer(text)) + \
                list(re.compile(rf"Top \d+ {re.escape(group)}\s+by [^:]+:\s+{re.escape(group)}\s+(?P<name>.+?)\s+[\d€$₹-]", re.I).finditer(text)):
            if any(text[m.start():m.start() + len(g)].lower() == g for g in longer):
                continue
            checked += 1
            # a claim about totals is checked against the total ranking, a claim about averages or rates against the others
            near = text[max(0, m.start() - 60): m.end() + 60].lower()
            want_total = "total" in near or m.re.pattern.startswith("Top")
            cands = [r for r in rankings if r.get("group") == group and r.get("top") and ((r.get("basis") == "total") == want_total)]
            named = [r for r in cands if r.get("measure") and str(r["measure"]).lower() in near]
            shown = {r["top"].strip().lower(): r["top"] for r in (named or cands)}
            if m.group("name").strip().lower() not in shown:
                wrong.append(f"'{m.group(0)[:60]}' but the computed top {group} is {', '.join(sorted(shown.values())) or 'unknown'}")
    if wrong:
        return False, "; ".join(wrong[:3])
    return True, f"{checked} claim(s) about the top group match the computed top" if checked else "no 'biggest' claim to check"


def rule_no_size_only_investment(md: str) -> tuple[bool, str]:
    """An action that recommends investing or spending must cite an effect, a significant or reliable difference, not just a large total."""
    items = numbered_blocks(first_list(sections(md, "What to do")))
    flagged = [normal(t.split("\n", 1)[0])[:60] for t in items if INVEST_WORDS.search(t.split("\n", 1)[0]) and not EVIDENCE_OK.search(t)]
    if flagged:
        return False, "investment advice without an effect or significance: " + " | ".join(flagged)
    return True, "every investment action cites an effect or a reliable difference" if items else "no actions"


def rule_coverage_warning(md: str, dates) -> tuple[bool, str]:
    """The warning appears exactly when under 70% of the calendar days between the first and last date have data."""
    from app.insights import coverage_warning
    expected = bool(coverage_warning(dates)) if dates is not None else False
    shown = COVERAGE_TEXT in md
    if expected == shown:
        return True, "coverage warning shown" if shown else "coverage is at least 70%, no warning"
    return False, "coverage is under 70% but the report has no warning" if expected else "the report warns about coverage that is at least 70%"


def rule_no_internal_names(md: str) -> tuple[bool, str]:
    """The main view shows no internal identifiers: snake_case names, double-underscore names or finding ids such as f3 or r4."""
    text = visible_text(main_view(md))
    found = sorted(set(re.findall(r"\b__\w+|\b[a-z]+(?:_[a-z0-9]+)+\b|\b[fr]\d+\b", text)))
    if found:
        return False, "internal names in the main view: " + ", ".join(found[:8])
    return True, "no internal names in the main view"


def rule_no_hash_links(md: str) -> tuple[bool, str]:
    """No link points at a #fragment or a hash route (the report is read as a file, outside the app)."""
    hits = re.findall(r"\]\(#[^)]*\)|href=\"#[^\"]*\"|#/(?:run|glossary|history)\b", md)
    if hits:
        return False, f"{len(hits)} hash link(s), for example {hits[0]}"
    return True, "no hash links"


def check(md: str, result: dict, dates=None) -> list[tuple[str, bool, str]]:
    actions = [a.get("text", "") for a in (result.get("report_view") or {}).get("actions") or []]
    rankings = [r for r in (result.get("summary_facts") or {}).get("rankings") or []]
    rows = [
        ("recommendation lists are identical", *rule_identical_lists(md, actions)),
        ("no action is a bare fact line", *rule_no_bare_fact_actions(md, actions)),
        ("'biggest' claims match the top group", *rule_biggest_matches_top(md, rankings)),
        ("no investment advice from size alone", *rule_no_size_only_investment(md)),
        ("coverage warning under 70% of days", *rule_coverage_warning(md, dates)),
        ("no internal column names in the main view", *rule_no_internal_names(md)),
        ("no hash links in the report", *rule_no_hash_links(md)),
    ]
    return rows


def run_dates(wd: Path, result: dict):
    """The date column of the cleaned data, or None when the run has none."""
    import pandas as pd
    p = wd / "clean.pkl"
    if not p.exists():
        return None
    df = pd.read_pickle(p)
    date_cols = [c["name"] for c in (result.get("profile") or {}).get("columns") or [] if c.get("role") == "date" and c["name"] in df.columns]
    return df[date_cols[0]] if date_cols else None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    target = Path(argv[1])
    wd = target if target.is_dir() else RUNS / argv[1]
    if not (wd / "result.json").exists():
        print(f"No finished run at {wd}")
        return 2
    sys.path.insert(0, str(ROOT / "backend"))
    result = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    md = (wd / "report.md").read_text(encoding="utf-8")
    rows = check(md, result, run_dates(wd, result))
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    failed = sum(not ok for _, ok, _ in rows)
    print(f"\n{len(rows) - failed} of {len(rows)} rules pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

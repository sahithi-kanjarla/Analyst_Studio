"""Summarise what Lens could not do and what people marked as unhelpful.

    python scripts/lens_feedback_report.py [run folder or runs folder ...]      # default: backend/data/runs

Reads lens_unmet.jsonl (a request no tool could do: text, tab, time) and lens_feedback.jsonl (a thumbs up or down with the plan and tools used) from each run folder and prints:
the most common unmet requests (grouped by their main words), the tabs they came from, the thumbs-down rate per tool, and the tool combinations that get the most thumbs-down. No data values are
in either file, so none are printed."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOP = set("the a an and or of to in on for is are was be my our i we me it its with about what how that this these those their your you please can could would should do does any some all show tell give make".split())


def read(path: Path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def run_dirs(args: list[str]) -> list[Path]:
    roots = [Path(a) for a in args] or [ROOT / "backend" / "data" / "runs"]
    dirs = []
    for r in roots:
        if (r / "lens_unmet.jsonl").exists() or (r / "lens_feedback.jsonl").exists():
            dirs.append(r)
        elif r.is_dir():
            dirs += [d for d in sorted(r.iterdir()) if d.is_dir() and ((d / "lens_unmet.jsonl").exists() or (d / "lens_feedback.jsonl").exists())]
    return dirs


def key(text: str) -> str:
    ws = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP]
    return " ".join(sorted(set(ws))[:4]) or text.lower()[:30]


def summarise(dirs: list[Path]) -> dict:
    unmet, fb = [], []
    for d in dirs:
        unmet += read(d / "lens_unmet.jsonl")
        fb += read(d / "lens_feedback.jsonl")
    groups: dict[str, list[dict]] = {}
    for u in unmet:
        groups.setdefault(key(u["request"]), []).append(u)
    top = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    tool_votes: dict[str, Counter] = {}
    combos: Counter = Counter()
    for f in fb:
        for t in (f.get("tools") or ["(none)"]):
            tool_votes.setdefault(t, Counter())[f["vote"]] += 1
        if f["vote"] == "down":
            combos[" + ".join(f.get("tools") or ["(none)"])] += 1
    return {"unmet": len(unmet), "feedback": len(fb), "top_unmet": [(k, len(v), v[0]["request"]) for k, v in top[:15]], "unmet_tabs": Counter(u.get("tab") or "?" for u in unmet).most_common(),
            "tool_votes": {t: dict(c) for t, c in tool_votes.items()}, "down_combos": combos.most_common(10),
            "down_requests": [f["request"] for f in fb if f["vote"] == "down"][:15]}


def main(argv: list[str]) -> int:
    dirs = run_dirs(argv)
    s = summarise(dirs)
    print(f"{len(dirs)} run folder(s); {s['unmet']} unmet request(s); {s['feedback']} thumbs")
    print("\nMost common unmet requests (grouped by main words):")
    for k, n, ex in s["top_unmet"] or [("(none)", 0, "")]:
        print(f"  {n:3d}  {k}   e.g. “{ex}”")
    print("\nUnmet requests by tab:", ", ".join(f"{t}: {n}" for t, n in s["unmet_tabs"]) or "(none)")
    print("\nThumbs per tool (up / down):")
    for t, c in sorted(s["tool_votes"].items(), key=lambda kv: -kv[1].get("down", 0)):
        print(f"  {t:20s} up {c.get('up', 0):3d}   down {c.get('down', 0):3d}")
    print("\nTool combinations with thumbs-down:")
    for c, n in s["down_combos"] or [("(none)", 0)]:
        print(f"  {n:3d}  {c}")
    print("\nRequests marked thumbs-down:")
    for r in s["down_requests"] or ["(none)"]:
        print("  -", r)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

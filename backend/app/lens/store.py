"""Lens state saved with the run (lens.json in the run folder), so the conversation, the pins and the question count survive a reload and live as long as the run does.
One file, versioned; a later stage adds its own keys (summaries, memory) next to "turns" and "pins" without touching these."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

VERSION = 1
MAX_TURNS = 300
MAX_CHIPS = 400
LOCK = threading.RLock()


def _path(wd: Path) -> Path:
    return Path(wd) / "lens.json"


def load(wd: Path) -> dict:
    p = _path(wd)
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        d = {}
    d.setdefault("v", VERSION)
    d.setdefault("turns", [])
    d.setdefault("pins", [])
    d.setdefault("chips", {})
    d.setdefault("asked", 0)
    return d


def save(wd: Path, d: dict) -> None:
    d["turns"] = d["turns"][-MAX_TURNS:]
    d["chips"] = dict(list(d["chips"].items())[-MAX_CHIPS:])
    p = _path(wd)
    tmp = p.with_name(f"lens.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d, default=str), encoding="utf-8")
    for attempt in range(10):         # on Windows a replace can briefly fail while another process holds the target
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
    p.write_text(json.dumps(d, default=str), encoding="utf-8")
    tmp.unlink(missing_ok=True)


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def add_turn(wd: Path, turn: dict, chips: list[dict] | None = None, count_ai: bool = False) -> dict:
    with LOCK:
        d = load(wd)
        d["turns"].append(turn)
        for c in chips or []:
            d["chips"][c["id"]] = {"text": c["text"], "query": c["query"]}
        if count_ai:
            d["asked"] += 1
        save(wd, d)
        return turn


def get_turn(wd: Path, turn_id: str) -> dict | None:
    return next((t for t in load(wd)["turns"] if t["id"] == turn_id), None)


def known_chip(wd: Path, chip_id: str) -> dict | None:
    return load(wd)["chips"].get(chip_id)


def add_pin(wd: Path, turn_id: str, target: str) -> dict | None:
    """Pins a copy of an answer (its sentence, table, chart address and how it was calculated). The original report is never touched. Pinning the same answer to the same place twice returns the first pin."""
    with LOCK:
        d = load(wd)
        t = next((x for x in d["turns"] if x["id"] == turn_id), None)
        if t is None or t["answer"].get("status") != "answered":
            return None
        old = next((p for p in d["pins"] if p["turn_id"] == turn_id and p["target"] == target), None)
        if old:
            return old
        a = t["answer"]
        pin = {"id": new_id(), "turn_id": turn_id, "target": target, "title": t["question"], "text": a["text"], "table": a.get("table"), "chart": a.get("chart"), "how": a.get("how"),
               "created": time.time()}
        d["pins"].append(pin)
        save(wd, d)
        return pin


def remove_pin(wd: Path, pin_id: str) -> bool:
    with LOCK:
        d = load(wd)
        keep = [p for p in d["pins"] if p["id"] != pin_id]
        if len(keep) == len(d["pins"]):
            return False
        d["pins"] = keep
        save(wd, d)
        return True

"""Critic agent: tries to break the analysts' conclusions before they reach the report.

Deterministic checks (always on):
  * multiple-testing correction across all group comparisons (Bonferroni)
  * small group sizes, heavy missingness, heavy imputation
  * skewed targets -> request an outlier-resistant robustness check (this triggers a follow-up round)
  * disagreement between mean-based and rank-based tests
Optional LLM review adds extra cautions and (validated) follow-up analyses."""
from __future__ import annotations

import json

from . import wording
from .llm import ask_json
from .planner import roles_and_uniques
from .tools import spec_signature, validate_spec


SENT_FINDINGS, SENT_CHARS = 8, 240          # what the reviewer is sent: the leading findings, each cut to its claim


def review(state: dict, llm) -> dict:
    findings = state["findings"]
    profile = state["profile"]
    plan = state["plan"]
    issues: list[dict] = []
    followups: list[dict] = []

    # --- multiple testing across group comparisons
    family = wording.comparison_family(findings)
    gc = [f for f in family if f["tool"] == "group_compare"]
    k = len(family)
    for f in family:
        p_adj = min(1.0, f["stats"]["p"] * k)
        f["stats"]["p_adjusted"] = round(p_adj, 6)
        f["stats"]["survives_correction"] = bool(p_adj < 0.05)
        if f["significant"] and p_adj >= 0.05:
            issues.append({"severity": "warn", "finding": f["id"],
                           "text": f"'{f['title']}' was significant on its own (p={f['stats']['p']:.3g}) but not after correcting for {k} comparisons (adjusted p={p_adj:.3g}); treat it as a lead, not a result."})
        small_below = {"eta_squared": 0.06, "cramers_v": 0.2, "rank_biserial": 0.2, "spearman_rho": 0.2}.get(f["stats"].get("effect_name"))
        if f["significant"] and small_below and (f["stats"].get("effect") or 0) < small_below:
            issues.append({"severity": "info", "finding": f["id"],
                           "text": f"'{f['title']}' is statistically detectable but the effect is small ({f['stats']['effect_name']}={f['stats']['effect']:.3f}), so it may not matter in practice; no action is recommended from it alone."})
        if f["stats"].get("min_group_n", 99) < 30:
            sev = "warn" if f["stats"]["min_group_n"] < 10 else "info"
            issues.append({"severity": sev, "finding": f["id"],
                           "text": f"'{f['title']}' includes a group with only {f['stats']['min_group_n']} rows, so its average is unstable."})
    if k >= 3:
        issues.append({"severity": "info", "finding": None, "text": f"{k} comparisons were run; Bonferroni-adjusted p-values are shown to limit false positives."})

    # --- robustness follow-ups (skewed numeric targets)
    done = {spec_signature({"tool": f["tool"], "params": f.get("params", {})}) for f in findings}
    for f in gc:
        st = f["stats"]
        if f["significant"] and st.get("kind") == "numeric" and abs(st.get("target_skew") or 0) > 1:
            spec = {"tool": "robust_compare", "params": dict(f["params"]), "why": "Target is heavily skewed, so confirm the group difference with a median-based test."}
            if spec_signature(spec) not in done and spec not in followups:
                followups.append(spec)
    for rc in [f for f in findings if f["tool"] == "robust_compare"]:
        match = next((g for g in gc if g["params"] == rc["params"]), None)
        if match and match["significant"] and not rc["significant"]:
            issues.append({"severity": "warn", "finding": match["id"],
                           "text": f"'{match['title']}' is significant by mean but not by median-based test: the effect may be driven by outliers."})
        elif match and match["significant"] and rc["significant"]:
            issues.append({"severity": "info", "finding": match["id"], "text": f"'{match['title']}' also holds with a median-based test (robust)."})

    # --- data quality
    tgt = plan.get("target")
    if tgt:
        miss = next((c["missing_pct"] for c in profile["columns"] if c["name"] == tgt), 0)
        if miss > 20:
            issues.append({"severity": "warn", "finding": None, "text": f"The target '{tgt}' is {miss:.0f}% missing; results describe only the rows that have a value."})
    for step in state.get("cleaning_log", []):
        if step["step"] == "Impute numeric" and step["count"] > 0.1 * profile["rows"]:
            issues.append({"severity": "info", "finding": None, "text": step["detail"] + " - heavy imputation can dampen real differences."})
    for f in findings:
        if f["tool"] == "trend" and f["stats"].get("periods", 99) < 6:
            issues.append({"severity": "warn", "finding": f["id"], "text": f"The trend rests on only {f['stats']['periods']} time periods; treat it as indicative."})
    if not any(f.get("significant") for f in findings):
        issues.append({"severity": "info", "finding": None, "text": "No statistically significant effects were found; the data may not contain a strong signal for this question, or there may be too little of it."})

    for w in (state.get("data_model") or {}).get("warnings", []):
        issues.append({"severity": w["severity"], "finding": None, "text": "Data connection: " + w["text"]})

    # --- optional LLM review
    notes: list[str] = []
    if llm is not None:
        summary = [{"id": f["id"], "title": f["title"], "text": f["text"].replace("**", "")[:SENT_CHARS], "p": f["stats"].get("p"), "n": f["stats"].get("n")} for f in findings][:SENT_FINDINGS]          # what the reviewer needs: the claim, its p and n
        out = ask_json(
            llm,
            "You are a skeptical senior statistician reviewing an analysis. Identify claims that the evidence does not fully support "
            "(causation vs correlation, confounding, small samples, selection bias). Optionally propose up to 2 extra analyses using tools "
            "group_compare/robust_compare/trend/pareto with exact column names. Return JSON: {\"cautions\": [up to 4 short strings], \"followups\": [{\"tool\",\"params\",\"why\"}]}",
            f"GOAL: {state['goal']}\nTARGET: {tgt}\nFINDINGS:\n{json.dumps(summary, default=str)}",
        )
        if out:
            notes = [str(c) for c in (out.get("cautions") or [])][:4]
            roles, uniques = roles_and_uniques(profile)
            for spec in (out.get("followups") or [])[:2]:
                ok, _ = validate_spec(spec if isinstance(spec, dict) else {}, roles, uniques)
                if ok and spec_signature(ok) not in done and ok not in followups:
                    followups.append(ok)
    dropped = []
    for n in notes:
        why = wording.contradicts(n, state) or ("talks about margin or profit, but the data has no cost, margin or profit measure" if wording.unsupported_profit_claim(n, state) else None)     # a limitation the findings contradict would mislead the reader
        if why:
            dropped.append({"text": n, "reason": why})
        else:
            issues.append({"severity": "info", "finding": None, "text": n, "source": "llm"})

    return {"issues": issues, "followups": followups, "approved": not followups, "dropped_notes": dropped}

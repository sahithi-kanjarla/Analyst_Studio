"""Optional business context: who the company is, what it sells, which KPIs it cares about.

Everything here is OPTIONAL. With no context the agents behave exactly as before. With context they:
  * pick the outcome column that matches your KPIs and industry,
  * use industry wording and levers in insights and strategies,
  * flag KPIs you mentioned that are missing from the data.

The context is user-supplied and unverified: it steers choices and wording, it is never treated as a fact about the data."""
from __future__ import annotations

import re

STOP = {"the", "and", "for", "with", "that", "this", "from", "our", "are", "was", "were", "have", "has", "had", "you", "your", "their", "its",
        "but", "not", "all", "any", "can", "will", "per", "how", "what", "why", "who", "which", "more", "most", "into", "over", "about",
        "data", "dataset", "analysis", "analyse", "analyze", "insights", "want", "need", "know", "find", "out", "get", "use", "using", "also"}

INDUSTRIES: dict[str, dict] = {
    "ecommerce": {
        "label": "E-commerce / Retail", "keywords": ["ecommerce", "e-commerce", "retail", "shop", "store", "fashion", "grocery", "d2c", "marketplace", "merchandis"],
        "target_hint": r"revenue|sales|gmv|aov|order_value|conversion|margin|units|basket|return",
        "customer": "customers", "value": "revenue, repeat purchase and margin",
        "levers": ["promotions and bundles", "loyalty and repeat purchase", "assortment and pricing"],
        "data_wants": [(r"cost|cogs|margin", "product cost or margin"), (r"channel|source|campaign|utm|medium", "acquisition channel or campaign"), (r"return|refund", "returns or refunds")],
    },
    "saas": {
        "label": "SaaS / Software", "keywords": ["saas", "software", "subscription", "platform", "app", "b2b software", "cloud"],
        "target_hint": r"mrr|arr|churn|retention|revenue|ltv|cac|nps|usage|activation|conversion|seats",
        "customer": "accounts", "value": "retention, expansion and recurring revenue",
        "levers": ["onboarding and activation", "expansion and upsell", "pricing and packaging"],
        "data_wants": [(r"plan|tier|subscription", "plan or subscription tier"), (r"usage|login|session|event", "product usage"), (r"signup|created|start|contract", "signup or contract dates")],
    },
    "finance": {
        "label": "Banking / Finance / Insurance", "keywords": ["bank", "finance", "financial", "insurance", "lending", "loan", "credit", "fintech", "payments"],
        "target_hint": r"default|loss|risk|balance|income|interest|fraud|approval|credit|delinquen|npl|claim",
        "customer": "clients", "value": "risk, loss rates and profitability",
        "levers": ["risk-based pricing", "underwriting rules", "early outreach and collections"],
        "data_wants": [(r"income|salary", "client income"), (r"score|rating", "credit score or rating"), (r"date|month|year", "a time dimension")],
    },
    "healthcare": {
        "label": "Healthcare", "keywords": ["health", "hospital", "clinic", "patient", "medical", "pharma", "care"],
        "target_hint": r"readmission|length_of_stay|los|mortality|cost|wait|satisfaction|outcome|diagnos",
        "customer": "patients", "value": "outcomes, cost and access to care",
        "levers": ["care pathways", "capacity and scheduling", "preventive outreach"],
        "data_wants": [(r"age", "patient age"), (r"department|ward|unit", "department or ward"), (r"date|admit|discharge", "admission dates")],
    },
    "education": {
        "label": "Education", "keywords": ["school", "university", "college", "education", "student", "edtech", "course", "learning"],
        "target_hint": r"score|grade|attendance|dropout|retention|pass|enrol|gpa|completion",
        "customer": "students", "value": "learning outcomes, attendance and retention",
        "levers": ["early-alert support", "teaching methods and curriculum", "attendance and engagement programmes"],
        "data_wants": [(r"attendance", "attendance"), (r"cohort|year|batch|term", "cohort or year"), (r"teacher|instructor|course|subject", "course or instructor")],
    },
    "manufacturing": {
        "label": "Manufacturing / Operations", "keywords": ["manufactur", "factory", "plant", "production", "industrial", "assembly"],
        "target_hint": r"defect|yield|downtime|output|units|scrap|cost|oee|throughput|cycle",
        "customer": "production runs", "value": "quality, yield and uptime",
        "levers": ["process control", "maintenance planning", "supplier quality"],
        "data_wants": [(r"machine|line|plant|station", "machine or line"), (r"shift", "shift"), (r"supplier|vendor", "supplier")],
    },
    "hr": {
        "label": "HR / People", "keywords": ["hr", "human resources", "people", "employee", "workforce", "talent", "recruit"],
        "target_hint": r"attrition|turnover|salary|performance|satisfaction|tenure|absence|engagement|promotion",
        "customer": "employees", "value": "retention, engagement and performance",
        "levers": ["manager effectiveness", "compensation and career paths", "onboarding and engagement"],
        "data_wants": [(r"department|team", "department or team"), (r"manager", "manager"), (r"tenure|join|hire", "tenure or hire date")],
    },
    "marketing": {
        "label": "Marketing / Advertising", "keywords": ["marketing", "advertis", "campaign", "agency", "media", "brand", "seo"],
        "target_hint": r"conversion|ctr|cpc|cpa|roas|leads|spend|impressions|clicks|revenue|cac",
        "customer": "audiences", "value": "return on spend and conversion",
        "levers": ["channel mix and budget allocation", "creative and messaging", "audience targeting"],
        "data_wants": [(r"cost|spend|budget", "spend"), (r"channel|source|campaign", "channel or campaign"), (r"date|week|month", "dates")],
    },
    "logistics": {
        "label": "Logistics / Supply chain", "keywords": ["logistic", "supply chain", "shipping", "freight", "delivery", "warehouse", "transport", "courier"],
        "target_hint": r"delay|late|delivery|cost|distance|lead_time|on_time|fuel|damage|transit",
        "customer": "shipments", "value": "on-time delivery and cost per shipment",
        "levers": ["route and carrier selection", "capacity planning", "exception handling"],
        "data_wants": [(r"carrier|vendor|courier", "carrier"), (r"origin|destination|route|region", "route or region"), (r"date|time", "timestamps")],
    },
    "hospitality": {
        "label": "Hospitality / Travel", "keywords": ["hotel", "hospitality", "travel", "tourism", "restaurant", "airline", "booking", "resort"],
        "target_hint": r"occupancy|adr|revpar|revenue|rating|booking|cancel|stay|nights",
        "customer": "guests", "value": "occupancy, rate and guest satisfaction",
        "levers": ["dynamic pricing", "channel and distribution mix", "guest experience"],
        "data_wants": [(r"channel|source", "booking channel"), (r"room|type", "room type"), (r"date|checkin|stay", "stay dates")],
    },
    "general": {
        "label": "General business", "keywords": [],
        "target_hint": r"revenue|sales|profit|cost|churn|conversion|score|rating",
        "customer": "records", "value": "the main outcome",
        "levers": ["effort and budget", "process improvements", "regular monitoring of the main KPI"],
        "data_wants": [],
    },
}

CURRENCIES = {"₹", "$", "€", "£"}


def _clip(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def detect_industry(ctx: dict) -> str:
    text = f"{ctx.get('industry', '')} {ctx.get('description', '')} {ctx.get('company', '')}".lower()
    best, best_hits = "general", 0
    for key, spec in INDUSTRIES.items():
        hits = sum(1 for k in spec["keywords"] if k in text)
        if ctx.get("industry") and spec["label"].lower() == ctx["industry"].lower():
            hits += 5
        if hits > best_hits:
            best, best_hits = key, hits
    return best


def normalise_context(raw: dict | None) -> dict:
    raw = raw or {}
    kpis_raw = raw.get("kpis") or []
    parts = kpis_raw if isinstance(kpis_raw, list) else re.split(r"[,;\n]+", str(kpis_raw))
    kpis = [k for k in (_clip(p, 80) for p in parts) if k][:10]
    cur = _clip(raw.get("currency"), 3)
    ctx = {"company": _clip(raw.get("company"), 120), "industry": _clip(raw.get("industry"), 120), "description": _clip(raw.get("description"), 1500),
           "kpis": kpis, "audience": _clip(raw.get("audience"), 200), "notes": _clip(raw.get("notes"), 1500),
           "currency": cur if cur in CURRENCIES else "", "explore": bool(raw.get("explore"))}
    ctx["industry_key"] = detect_industry(ctx)
    return ctx


def has_context(ctx: dict | None) -> bool:
    return bool(ctx) and any(ctx.get(k) for k in ("company", "industry", "description", "kpis", "audience", "notes"))


def lens(ctx: dict | None) -> dict:
    spec = dict(INDUSTRIES.get((ctx or {}).get("industry_key") or "general", INDUSTRIES["general"]))
    spec["target_rx"] = re.compile(spec["target_hint"], re.I)
    return spec


def context_text(ctx: dict | None) -> str:
    """Plain-text summary for LLM prompts. Only what the user typed, nothing from the data."""
    defs = (ctx or {}).get("definitions_text")
    if not has_context(ctx):
        return f"Business definitions (user-written, unverified):\n{defs}" if defs else ""
    lines = []
    if ctx.get("company"):
        lines.append(f"Company: {ctx['company']}")
    if ctx.get("industry") or ctx.get("industry_key") != "general":
        lines.append(f"Industry: {ctx.get('industry') or lens(ctx)['label']}")
    if ctx.get("description"):
        lines.append(f"About the business: {ctx['description']}")
    if ctx.get("kpis"):
        lines.append("KPIs they care about: " + ", ".join(ctx["kpis"]))
    if ctx.get("audience"):
        lines.append(f"Audience for the results: {ctx['audience']}")
    if ctx.get("notes"):
        lines.append(f"Other notes: {ctx['notes']}")
    if defs:
        lines.append(f"Business definitions (user-written, unverified):\n{defs}")
    return "\n".join(lines)


def tokens(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", str(text).lower()):
        if len(t) >= 3 and t not in STOP:
            out.add(t)
            if len(t) > 3 and t.endswith("s"):
                out.add(t[:-1])
    return out


GENERIC_KPI = {"customer", "customers", "client", "clients", "user", "users", "total", "average", "avg", "mean", "rate", "number", "count", "net", "gross",
               "monthly", "annual", "weekly", "daily", "overall", "per", "new", "average", "growth"}


def kpi_tokens(ctx: dict | None, mapped: dict | None = None) -> set[str]:
    """Meaningful words of the KPIs the user named. Generic words ('customer', 'total', 'rate') are dropped so that
    'customer satisfaction' is not 'found' just because some column contains the word customer.
    A KPI the analysis brief mapped to a column (`mapped` {kpi: column or None}) is matched by that column instead, so its words are left out."""
    out: set[str] = set()
    for k in (ctx or {}).get("kpis", []):
        if (mapped or {}).get(k):
            continue
        t = tokens(k)
        out |= (t - GENERIC_KPI) or t
    return out


def description_tokens(ctx: dict | None) -> set[str]:
    c = ctx or {}
    return tokens(f"{c.get('description', '')} {c.get('notes', '')}")

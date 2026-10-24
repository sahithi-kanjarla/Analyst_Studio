import { ReactNode } from "react";

export type AgentDef = {
  id: string;
  phase: number;
  role: string;
  brain: "code" | "llm";
  does: string;
  out: string;
  icon: ReactNode;
};

export const PHASES = [
  { n: 1, title: "Understand", sub: "Read & connect the data" },
  { n: 2, title: "Plan & prepare", sub: "Decide what to analyse" },
  { n: 3, title: "Analyse & verify", sub: "Compute, then challenge" },
  { n: 4, title: "Deliver", sub: "Explain & hand over" },
];

const I = (d: ReactNode) => d;

export const AGENTS: AgentDef[] = [
  {
    id: "Profiler", phase: 1, role: "Reads every column", brain: "code",
    does: "Detects the role of every column (id, number, yes/no, category, date, text), including numbers and dates stored as text.",
    out: "A typed profile of each table: rows, columns, duplicates, missing values.",
    icon: I(<><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M4 10h16M10 4v16" /></>),
  },
  {
    id: "Joiner", phase: 1, role: "Links multiple files", brain: "llm",
    does: "With two or more files it finds the shared keys, picks the analysis grain from your goal, joins safely and measures every join.",
    out: "One analysis table plus a data model showing how the files connect.",
    icon: I(<><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="M8 8l8 8" /></>),
  },
  {
    id: "Clarifier", phase: 1, role: "Asks only what matters", brain: "llm",
    does: "Spots ambiguities in the data (two dates, placeholder values, negatives) and asks a few short questions. Zero questions when nothing is unclear.",
    out: "At most 6 questions; skipping them never changes a number.",
    icon: I(<><path d="M9 9a3 3 0 115 2c-1 .8-2 1.2-2 3" /><circle cx="12" cy="18" r=".6" /></>),
  },
  {
    id: "Planner", phase: 2, role: "Turns goal into a plan", brain: "llm",
    does: "Converts your goal into questions, picks the target and only the relevant columns, and chooses the analyses to run.",
    out: "An analysis plan. Anything the LLM invents is validated against real columns.",
    icon: I(<><path d="M5 5h14M5 12h10M5 19h6" /></>),
  },
  {
    id: "Cleaner", phase: 2, role: "Fixes the data, logs it", brain: "code",
    does: "Fixes types, standardises missing values, removes duplicates, imputes and flags outliers, only for the columns the plan needs.",
    out: "A cleaned analysis copy and a full audit log. Original values are never edited.",
    icon: I(<><path d="M4 20l9-9M13 11l3-7 4 4-7 3z" /></>),
  },
  {
    id: "Analyst", phase: 3, role: "Runs the statistics", brain: "code",
    does: "Runs the planned analyses from a tested toolbox: tests, effect sizes, trends, segments, charts. Every row is used.",
    out: "Findings with real numbers and charts.",
    icon: I(<><path d="M3 20h18" /><path d="M6 16v-5M11 16V6M16 16v-8" /></>),
  },
  {
    id: "Critic", phase: 3, role: "Challenges every result", brain: "llm",
    does: "Applies multiple-testing correction, checks sample size, skew and practical vs statistical significance. Can send the Analyst back for robustness checks.",
    out: "Approved findings, each with a confidence level.",
    icon: I(<><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" /></>),
  },
  {
    id: "Insights", phase: 4, role: "Explains each chart", brain: "llm",
    does: "Writes what we see, why it matters, how much to trust it, possible explanations to test and what to check next.",
    out: "One insight card per chart.",
    icon: I(<><path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0012 3z" /></>),
  },
  {
    id: "Strategist", phase: 4, role: "Proposes testable moves", brain: "llm",
    does: "Turns only significant, correction-surviving findings into ranked strategies with pilots, KPIs, risks, effort and time horizon.",
    out: "Strategies, or an honest 'no strong lever found'.",
    icon: I(<><path d="M5 19V5M5 5h11l-2 4 2 4H5" /></>),
  },
  {
    id: "Reporter", phase: 4, role: "Assembles the report", brain: "llm",
    does: "Builds the report. The LLM only writes the summary and recommendations; every number comes from the tools.",
    out: "A report that opens with a direct answer to your goal.",
    icon: I(<><path d="M7 3h8l4 4v14H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></>),
  },
  {
    id: "Power BI", phase: 4, role: "Starter pack", brain: "code",
    does: "Exports cleaned data, DAX measures, a data dictionary, a theme and a guide so you can continue in Power BI.",
    out: "A downloadable powerbi_pack.zip.",
    icon: I(<><path d="M5 12h14M13 6l6 6-6 6" /></>),
  },
];

export const ORDER = AGENTS.map((a) => a.id);
export const ALIAS: Record<string, string> = { "Analyst brief": "Clarifier" };

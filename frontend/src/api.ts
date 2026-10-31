export interface AgentEvent {
  t: number;
  agent: string;
  message: string;
  data?: Record<string, unknown>;
}

export interface Insight {
  headline: string;
  observation: string;
  why_it_matters: string;
  hypotheses: string[];
  check_next: string[];
  confidence: { level: "high" | "medium" | "low" | "exploratory" | "descriptive"; label: string; reasons: string[] };
  kpi_related: boolean;
  score: number;
  source: "rules" | "llm";
}

export interface Strategy {
  id: string;
  rank: number;
  kind: "data" | "foundation";
  title: string;
  theme: string;
  objective: string;
  rationale: string;
  evidence: string[];
  actions: string[];
  how_to_test: string;
  kpis: string[];
  risks: string[];
  impact: "High" | "Medium" | "Low";
  effort: "High" | "Medium" | "Low";
  horizon: string;
  confidence: string;
  source: "rules" | "llm";
  merged_from?: string[];
}

export interface CardTable {
  id: string;
  title: string;
  columns: string[];
  rows: (string | number | null)[][];
  total: number;
}

export interface CardPart {
  id: string;
  target: string;
  headline: string;
  observation: string;
  checks: string[];
  tables: CardTable[];
}

/** One card of the report: a finding with its median check, its ratio and its per-group tables folded in. */
export interface ReportCard {
  id: string;
  members: string[];
  tool: string;
  title: string;
  tier: number;
  headline: string;
  observation: string;
  why_it_matters: string;
  confidence: Insight["confidence"];
  kpi_related: boolean;
  chart?: string;
  chart_url?: string;
  checks: string[];
  tables: CardTable[];
  also: CardPart[];
  hypotheses: string[];
  check_next: string[];
  stats: { n?: number; p?: number; p_adjusted?: number };
}

export interface ReportView {
  gallery?: { key: string[]; more: string[] };
  answers: { question: string; answer: string }[];
  numbers: { label: string; value: string; note: string }[];
  headlines: { id: string; members: string[]; text: string }[];
  actions: { text: string; evidence: string[] }[];
  evidence: ReportCard[];
  appendix: ReportCard[];
  checked: { id: string; line: string; check: string }[];
  profile: { distributions: { id: string; line: string }[]; outliers: { id: string; line: string }[]; composition: { id: string; line: string }[]; tables: CardTable[] };
  strategies: { strategies: Strategy[]; foundation: { id: string; title: string; line: string }[]; dropped: number };
  page?: ReportPageData;
}

/** The main view of the report as data: the same cards, actions and figures as the markdown, in the same words. */
export interface ReportPageData {
  title: string;
  company: string;
  date: string;
  mode: string;
  goal: string;
  trust: string;
  headline: string;
  headline_more: string;
  tiles: { label: string; value: string }[];
  cards: { num: number; question: string; lines: string[]; trust: string; trust_why: string; so_what: string }[];
  actions: { text: string; question: number | null; chart: string; chart_title: string; figure: string; chart_url?: string }[];
  charts: { title: string; path: string; caption: string; details: string[]; url?: string }[];
  no_action: string[];
  behind_md: string;
}

export interface ChatAnswer {
  status: "answered" | "no_fit" | "unavailable";
  text: string;
  rows?: (string | number)[][];
  chart_url?: string;
  suggestions: string[];
  op?: string;
  how?: { operation: string; columns: string[]; detail: string };
}
export interface ChatEntry { id: string; question: string; answer: ChatAnswer; pinned: boolean; at: number }
export interface AskInfo { enabled: boolean; ai: boolean; chips: { id: string; text: string }[]; history: ChatEntry[] }

export interface DataGap {
  severity: "warn" | "info";
  title: string;
  detail: string;
  suggestion: string;
}

export interface KeyMetric {
  label: string;
  value: string;
  note: string;
}

export interface BusinessContext {
  company?: string;
  industry?: string;
  description?: string;
  kpis?: string[];
  audience?: string;
  notes?: string;
  currency?: string;
  explore?: boolean;
  industry_key?: string;
}

export interface BusinessContextInput {
  company: string;
  industry: string;
  description: string;
  kpis: string;
  audience: string;
  notes: string;
  currency: string;
  explore: boolean;
  review_saved?: boolean;
}

export interface FindingTable {
  columns: string[];
  rows: (string | number)[][];
  total?: number;
}

export interface DerivedMetric {
  name: string;
  a: string;
  b: string;
  meaning: string;
  part_of?: boolean;
}

export interface GoalAnswer {
  sentence: string;
  groups_sentence: string;
  finding_ids: string[];
}

export interface Finding {
  insight?: Insight;
  table?: FindingTable;
  chart_type?: string;
  chart_why?: string;
  id: string;
  tool: string;
  title: string;
  text: string;
  chart_url?: string;
  significant: boolean | null;
  action?: string | null;
  why?: string;
  stats: Record<string, unknown> & { p?: number; p_adjusted?: number; survives_correction?: boolean; n?: number };
}

export interface CleaningStep {
  step: string;
  detail: string;
  count: number;
}

export interface Issue {
  severity: "warn" | "info";
  text: string;
}

export interface Relationship {
  child_table: string;
  child_col: string;
  parent_table: string;
  parent_col: string;
  cardinality: string;
  child_row_match: number;
  score: number;
  status: "used" | "skipped" | "rejected" | "unused";
  reason: string;
  method: "join" | "aggregate" | "";
}

export interface DataModel {
  tables: { name: string; rows: number; cols: number; columns?: { name: string; role: string }[] }[];
  base_table: string;
  base_reason: string;
  relationships: Relationship[];
  steps: { type: string; table: string; detail: string; rows_before: number; rows_after: number }[];
  warnings: { severity: "warn" | "info"; text: string }[];
  unused_tables: string[];
  origin: Record<string, string>;
}

export interface QuestionOption {
  value: string;
  label: string;
}

export interface Question {
  id: string;
  kind: string;
  text: string;
  options: QuestionOption[];
  default: string;
  prefill?: string;
  prefill_source?: "glossary" | "default";
  why: string;
  columns: string[];
}

export interface AnswerRow {
  id: string;
  text: string;
  options: QuestionOption[];
  default: string;
  answer: string;
  source: string;
  reused: boolean;
  saved: string;
}

export interface QuestionSet {
  status: string;
  workspace: string;
  questions: Question[];
  notes: string;
  definitions: Record<string, string>;
  can_skip: boolean;
}

export interface AppliedDefinition {
  id: string;
  question: string;
  answer: string;
  answer_label: string;
  source: "user" | "default" | "glossary" | "goal";
  set_by?: string;
  effect: string;
  rows: number | null;
}

export interface GlossaryAnswer {
  value: string;
  label?: string;
  question: string;
  updated?: number;
}

export interface GlossaryEntry {
  answers: Record<string, GlossaryAnswer>;
  definitions: Record<string, string>;
  notes: string;
}

export interface Decision {
  decision: string;
  source: "llm" | "repaired" | "fallback" | "rules-only";
  chosen: unknown;
  why: string;
  errors: string[];
}

export interface RunResult {
  profile?: { rows: number; cols: number };
  decisions?: Decision[];
  run_mode?: string;
  stage_sources?: Record<string, string>;
  context?: BusinessContext;
  strategies?: Strategy[];
  strategy_summary?: string;
  data_gaps?: DataGap[];
  key_metrics?: KeyMetric[];
  data_model?: DataModel | null;
  run_id: string;
  status: "queued" | "running" | "needs_input" | "done" | "error";
  goal: string;
  error?: string;
  llm_mode?: string;
  report_md?: string;
  report_view?: ReportView;
  findings?: Finding[];
  cleaning_log?: CleaningStep[];
  critique?: { issues: Issue[] };
  plan?: {
    objective: string;
    questions: string[];
    target: string | null;
    date_col: string | null;
    relevant_columns: string[];
    analyses: { tool: string; params: Record<string, unknown>; why: string; origin?: "ai" | "code" | "rules" }[];
    skipped: { column: string; reason: string }[];
    source: string;
  };
  applied_definitions?: AppliedDefinition[];
  derived_metrics?: { created: DerivedMetric[]; rejected: { a: string; b: string; reason: string }[]; removed: DerivedMetric[] };
  goal_answer?: GoalAnswer | null;
  powerbi?: { files: string[]; measures: string[]; visuals: { visual: string; axis: string; values: string; tip: string }[] };
}

export interface DataPage {
  columns: string[];
  total: number;
  offset: number;
  limit: number;
  rows: (string | number | boolean | null)[][];
}

export interface ColumnChange {
  name: string;
  in_raw: boolean;
  in_clean: boolean;
  type_before: string | null;
  type_after: string | null;
  missing_before: number | null;
  missing_after: number | null;
  unique_after: number | null;
  changed: boolean;
}

export interface DataCompare {
  rows_before: number;
  rows_after: number;
  columns: ColumnChange[];
}

export interface DashMeasure {
  col: string;
  label: string;
  agg: "sum" | "avg" | "ratio";
  den?: string;
  fmt: "num" | "money" | "pct";
  note: string;
}

export interface DashData {
  title: string;
  objective: string;
  currency: string;
  date: string | null;
  date_label: string | null;
  date_coverage?: number | null; // share of calendar days between the first and last date that have a row
  measures: DashMeasure[];
  dims: { col: string; unique: number; score: number; top?: number }[];
  columns: string[];
  rows: (string | number | null)[][];
  total_rows: number;
  sampled: boolean;
}

export interface DataSource {
  id: string;
  label: string;
  kind: "cleaned" | "joined" | "original";
  rows: number;
}

export interface RunSummary {
  run_id: string;
  goal: string;
  status: string;
  filename?: string;
  created?: number;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

/** The shipped sample run (backend DEMO_RUN_ID): read-only, so nothing that re-runs it is offered. */
export const DEMO_RUN_ID = "5a1e5d3e0001";
export const isDemo = (id: string) => id === DEMO_RUN_ID;

export const api = {
  health: () => fetch("/api/health").then((r) => json<{ status: string; llm: string; chatbot?: string }>(r)),
  listRuns: () => fetch("/api/runs").then((r) => json<RunSummary[]>(r)),
  getRun: (id: string) => fetch(`/api/runs/${id}`).then((r) => json<RunResult>(r)),
  createRun: (files: File[], goal: string, ctx?: BusinessContextInput) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("goal", goal);
    if (ctx) {
      (["company", "industry", "description", "kpis", "audience", "notes", "currency"] as const).forEach((k) => ctx[k] && fd.append(k, ctx[k]));
      if (ctx.explore) fd.append("explore", "true");
      if (ctx.review_saved) fd.append("review_saved", "true");
    }
    return fetch("/api/runs", { method: "POST", body: fd }).then((r) => json<{ run_id: string }>(r));
  },
  askInfo: (id: string) => fetch(`/api/runs/${id}/ask`).then((r) => json<AskInfo>(r)),
  ask: (id: string, body: { question?: string; chip?: string }) =>
    fetch(`/api/runs/${id}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => json<ChatEntry>(r)),
  pin: (id: string, entry: string, pinned: boolean) =>
    fetch(`/api/runs/${id}/ask/pin`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: entry, pinned }) }).then((r) => json<ChatEntry>(r)),
  openDemo: () => fetch("/api/demo", { method: "POST" }).then((r) => json<{ run_id: string; read_only: boolean }>(r)),
  removeDerived: (id: string, names: string[]) =>
    fetch(`/api/runs/${id}/remove-derived`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ names }) }).then((r) => json<{ run_id: string }>(r)),
  getAnswersReview: (id: string) => fetch(`/api/runs/${id}/answers-review`).then((r) => json<{ workspace: string; status: string; answers: AnswerRow[] }>(r)),
  rerunWithAnswers: (id: string, answers: Record<string, string>, ask_again: string[]) =>
    fetch(`/api/runs/${id}/rerun-answers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answers, ask_again }) }).then((r) => json<{ run_id: string }>(r)),
  getDashboard: (id: string) => fetch(`/api/runs/${id}/dashboard`).then((r) => json<DashData>(r)),
  getSources: (id: string) => fetch(`/api/runs/${id}/sources`).then((r) => json<DataSource[]>(r)),
  downloadUrl: (id: string, source: string) => `/api/runs/${id}/data/download?source=${encodeURIComponent(source)}`,
  getData: (id: string, source: string, offset: number, limit: number, q: string, sort: string, desc: boolean) =>
    fetch(`/api/runs/${id}/data?source=${source}&offset=${offset}&limit=${limit}&q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sort)}&desc=${desc}`).then((r) => json<DataPage>(r)),
  getCompare: (id: string) => fetch(`/api/runs/${id}/data/compare`).then((r) => json<DataCompare>(r)),
  cleanedCsvUrl: (id: string) => `/api/runs/${id}/files/cleaned.csv`,
  powerbiUrl: (id: string) => `/api/runs/${id}/powerbi`,
  eventsUrl: (id: string) => `/api/runs/${id}/events`,
  getQuestions: (id: string) => fetch(`/api/runs/${id}/questions`).then((r) => json<QuestionSet>(r)),
  submitAnswers: (id: string, body: { answers: Record<string, string>; notes: string; definitions: Record<string, string>; skip: boolean }) =>
    fetch(`/api/runs/${id}/answers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => json<{ status: string }>(r)),
  listGlossary: () => fetch("/api/glossary").then((r) => json<{ workspaces: Record<string, GlossaryEntry> }>(r)),
  putGlossary: (workspace: string, entry: GlossaryEntry) =>
    fetch(`/api/glossary?workspace=${encodeURIComponent(workspace)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(entry) }).then((r) => json<GlossaryEntry>(r)),
  deleteGlossary: (workspace: string) => fetch(`/api/glossary?workspace=${encodeURIComponent(workspace)}`, { method: "DELETE" }).then((r) => json<{ deleted: string }>(r)),
};

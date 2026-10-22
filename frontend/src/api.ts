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
}

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
}

export interface FindingTable {
  columns: string[];
  rows: (string | number)[][];
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

export interface RunResult {
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
  findings?: Finding[];
  cleaning_log?: CleaningStep[];
  critique?: { issues: Issue[] };
  plan?: {
    objective: string;
    questions: string[];
    target: string | null;
    date_col: string | null;
    relevant_columns: string[];
    analyses: { tool: string; params: Record<string, unknown>; why: string }[];
    skipped: { column: string; reason: string }[];
    source: string;
  };
  applied_definitions?: AppliedDefinition[];
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
  agg: "sum" | "avg";
  fmt: "num" | "money" | "pct";
  note: string;
}

export interface DashData {
  title: string;
  objective: string;
  currency: string;
  date: string | null;
  date_label: string | null;
  measures: DashMeasure[];
  dims: { col: string; unique: number; score: number }[];
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

export const api = {
  health: () => fetch("/api/health").then((r) => json<{ status: string; llm: string }>(r)),
  listRuns: () => fetch("/api/runs").then((r) => json<RunSummary[]>(r)),
  getRun: (id: string) => fetch(`/api/runs/${id}`).then((r) => json<RunResult>(r)),
  createRun: (files: File[], goal: string, ctx?: BusinessContextInput) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("goal", goal);
    if (ctx) {
      (["company", "industry", "description", "kpis", "audience", "notes", "currency"] as const).forEach((k) => ctx[k] && fd.append(k, ctx[k]));
      if (ctx.explore) fd.append("explore", "true");
    }
    return fetch("/api/runs", { method: "POST", body: fd }).then((r) => json<{ run_id: string }>(r));
  },
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

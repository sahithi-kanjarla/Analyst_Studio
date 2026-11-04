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
  /** Several findings with the same leading group, merged by the selection step; every figure is the original finding's own. */
  /** Built by code from the saved finding for the Report card: lead figure and label, a plain line, the bars or monthly series, and the plain-words detail. */
  lead?: { value: string; label: string; sign: number; line: string; bars: { label: string; value: number; text: string }[]; bars_total: number; series: { key: string; label: string; value: number }[]; ratio: string; difference: string; why: string; merged: string[] };
  merged?: { dimension: string; leader: string; items: { id: string; measure: string; figure: number | null; text: string }[] };
}

export interface NullLine { id: string; title: string; kind: string; reason: string }

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
  strategies: { strategies: Strategy[]; foundation: { id: string; title: string; line: string }[]; dropped: number; more?: Strategy[] };
  page?: ReportPageData;
  report_findings?: ReportCard[];
  more_findings?: ReportCard[];
  null_lines?: NullLine[];
  null_counted?: number;
  left_out_count?: number;
  /** Set when no goal (or a vague one) was given: the Report says it is an overview. */
  overview_note?: string;
  main_measure?: string;
}

/** The main view of the report as data: the same cards, actions and figures as the markdown, in the same words. */
export interface ReportPageData {
  title: string;
  company: string;
  date: string;
  mode: string;
  goal: string;
  trust: string;
  ai_partly?: string[];
  headline: string;
  headline_more: string;
  tiles: { label: string; value: string }[];
  cards: { num: number; question: string; lines: string[]; trust: string; trust_why: string; so_what: string }[];
  actions: { text: string; question: number | null; chart: string; chart_title: string; figure: string; chart_url?: string; split_lead?: string; split_line?: string; split_rest?: string }[];
  charts: { title: string; path: string; caption: string; details: string[]; url?: string }[];
  no_action: string[];
  behind_md: string;
  more?: { title: string; md: string }[];
  scope?: string;
  built_title?: string;
  built_answer?: string;
  built_tag?: string;
  footnotes?: string[];
}

export interface LensTable { columns: string[]; rows: (string | number | null)[][]; shown: string[][]; total_rows: number; truncated: boolean }
export interface LensChart { type: string; path: string | null; url: string; note: string }
export interface BriefLine { text: string; ids: string[]; edited?: boolean }
export interface Brief {
  id: string; title: string; title_edited?: boolean; audience: string; focus: string; source: string; no_info: string; edited: number;
  headline: BriefLine | null; key_figures: BriefLine[]; recommendations: BriefLine[]; charts: { path: string; title: string }[];
  quality: { trust: string; notes: string[] };
}
export interface LensHow {
  operation: string;
  /** Custom analysis: the SQL (shown collapsed) and its plain description. */
  sql?: string;
  plain?: string;
  steps?: { n: number; tool: string; summary: string; ok: boolean }[];
  /** The lines the box shows, written once on the server so it is never empty. */
  lines?: string[];
  columns?: { name: string; label: string; role: string }[];
  filters?: string[];
  rows_total?: number;
  rows_used?: number;
  rows_left_out?: number;
  left_out?: { reason: string; rows: number }[];
  groups_not_shown?: number;
  notes?: string[];
  sources?: string[];
  fact_ids?: string[];
}
export interface LensControls { chart_types: string[]; grains: string[]; can_top_n: boolean; chart: string }
export interface LensChip { id: string; text: string }
export interface LensAnswer {
  more?: LensChip[];
  briefs?: Brief[];
  export?: { what: string; label: string; url: string; browser: boolean; text?: string };
  second_method?: { label: string; text: string; table: LensTable };
  disagree?: boolean;
  unmet?: boolean;
  feedback?: "up" | "down";
  agent?: { plan: { tool: string; args: Record<string, unknown> }[]; tools: string[]; steps: { n: number; tool: string; summary: string; ok: boolean }[]; calls: number };
  view?: { changed: boolean; commands?: ViewCommand[]; undo?: boolean; state?: ViewState; preview?: boolean; count?: number };
  focus?: { id: string; title: string };
  status: "answered" | "clarify" | "no_fit" | "unavailable" | "limit";
  /** data (default) | why | app | summary | priority | strategy | general | view */
  kind?: string;
  /** A short label above the answer: "Suggestion", "General knowledge, not from your data", ... */
  label?: string;
  lines?: string[];
  actions?: string[];
  fact_ids?: string[];
  view_offer?: { name: string; label: string };
  premise?: { text: string };
  text: string;
  query?: Record<string, unknown>;
  table?: LensTable;
  chart?: LensChart;
  controls?: LensControls;
  how?: LensHow;
  chips: LensChip[];
  clarify?: { question: string; choices: { id: string; label: string }[] };
  ai_calls: number;
  source?: string;
}
export interface LensTurn { id: string; at: number; question: string; tab: string; answer: LensAnswer }
export interface LensPin { id: string; turn_id: string; target: "report" | "dashboard"; title: string; text: string; table?: LensTable; chart?: LensChart; how?: LensHow; created: number }
export interface LensView { name: string; title: string; lines: string[]; actions: string[]; fact_ids: string[]; from_turn: string; created: number }
export interface LensInfo { enabled: boolean; ai: boolean; chips: LensChip[]; more?: LensChip[]; history: LensTurn[]; pins: LensPin[]; views?: Record<string, LensView>; limit: { asked: number; max: number } }
export interface ViewState { hidden: string[]; short: Record<string, boolean>; top_n: Record<string, number>; only_supporting: Record<string, boolean>; chart: Record<string, { chart?: string; grain?: string; n?: number }>; order: Record<string, string[]>; titles: Record<string, string>; pinned: string[]; details?: boolean; history: unknown[] }
export interface ViewTab { order: string[]; hidden: string[]; folded: string[]; short: boolean; top_n: number | null; only_supporting: boolean; items: string[]; sections: string[] }
export interface ViewItemInfo { id: string; tab: string; kind: "section" | "card" | "chart" | "insight" | "action" | "strategy"; title: string; section: string | null; score?: number; changeable?: string[]; card?: string }
export interface ViewData {
  state: ViewState;
  resolved: { tabs: Record<string, ViewTab>; titles: Record<string, string>; chart: Record<string, { chart?: string; grain?: string; n?: number }>; pinned: string[]; hidden: string[]; original: boolean; details?: boolean };
  redrawn: Record<string, { url: string; note: string }>;
  message: string;
  bar?: { show: boolean; text: string; hidden: number };
  can_undo: boolean;
  items: Record<string, ViewItemInfo>;
  sections: Record<string, string[]>;
  local?: boolean;
}
export type ViewCommand = Record<string, unknown> & { cmd: string };
export type LensAsk = { question?: string; chip?: string; choice?: { turn: string; choice: string }; patch?: { turn: string; set: Record<string, unknown> }; tab?: string ; focus?: { id: string }; view_state?: ViewState };

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
  ai_usage?: { calls?: number; sent?: number; cached?: number; input_tokens?: number; output_tokens?: number; seconds?: number } | null;
  profile?: { rows: number; cols: number; columns?: { name: string; dtype: string; role: string; missing_pct: number; unique: number }[] };
  understanding?: { source?: string; columns?: Record<string, { display_name?: string; meaning?: string; role?: string }> };
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
  plan: DashPlan;
}

/** Which charts the dashboard shows (chosen by the saved selection), the filters worth showing, and where each tile sits on a 24 x 24 grid that is fully covered. */
export interface DashTile {
  id: string;
  kind: "trend" | "dim" | "finding";
  col?: string;
  finding_id: string;
  importance: number;
  title: string;
  items?: { measure: string; text: string }[];
}
export interface DashRect { id: string; x: number; y: number; w: number; h: number }
export interface DashPlan {
  curated: boolean;
  tiles: DashTile[];
  more: DashTile[];
  filters: string[];
  layout: { cols: number; rows: number; tiles: DashRect[] };
  objective: string;
  mode: "goal" | "overview";
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

/** The access code the visitor typed on the upload panel (kept for this tab only). The server asks for it only when UPLOADS_REQUIRE_CODE is set. */
export function getAccessCode(): string {
  try {
    return sessionStorage.getItem("access_code") ?? "";
  } catch {
    return "";
  }
}
export function setAccessCode(code: string) {
  try {
    sessionStorage.setItem("access_code", code);
  } catch {
    /* storage can be blocked: the code is then asked for again */
  }
}
const JSON_HEADERS = (): Record<string, string> => ({ "Content-Type": "application/json", ...(getAccessCode() ? { "X-Access-Code": getAccessCode() } : {}) });

export const api = {
  health: () => fetch("/api/health").then((r) => json<{ status: string; version: string }>(r)),
  siteConfig: () => fetch("/api/config").then((r) => json<{ llm: string; chatbot?: string; requires_code?: boolean; public_demo?: boolean; retention_days?: number; daily_ai_runs?: number; daily_lens_questions?: number }>(r)),
  deleteRun: (id: string) => fetch(`/api/runs/${id}`, { method: "DELETE" }).then((r) => json<{ deleted: string; files_removed: number }>(r)),
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
    if (getAccessCode()) fd.append("access_code", getAccessCode());
    return fetch("/api/runs", { method: "POST", body: fd }).then((r) => json<{ run_id: string }>(r));
  },
  viewGet: (id: string) => fetch(`/api/runs/${id}/view`).then((r) => json<ViewData>(r)),
  viewApply: (id: string, body: { command?: ViewCommand; commands?: ViewCommand[]; state?: ViewState }) =>
    fetch(`/api/runs/${id}/view`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => json<ViewData>(r)),
  briefsList: (id: string) => fetch(`/api/runs/${id}/briefs`).then((r) => json<{ briefs: Brief[] }>(r)),
  briefRename: (id: string, brief: string, title: string) =>
    fetch(`/api/runs/${id}/briefs/${brief}/rename`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) }).then((r) => json<Brief>(r)),
  briefEdit: (id: string, brief: string, section: string, index: number | null, text: string) =>
    fetch(`/api/runs/${id}/briefs/${brief}/edit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ section, index, text }) }).then((r) => json<Brief>(r)),
  briefDelete: (id: string, brief: string) => fetch(`/api/runs/${id}/briefs/${brief}`, { method: "DELETE" }).then((r) => json<{ deleted: string }>(r)),
  lensFeedback: (id: string, turn: string, vote: "up" | "down") =>
    fetch(`/api/runs/${id}/lens/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ turn, vote }) }).then((r) => json<{ recorded: string }>(r)),
  lensInfo: (id: string, tab = "") => fetch(`/api/runs/${id}/lens${tab ? `?tab=${encodeURIComponent(tab)}` : ""}`).then((r) => json<LensInfo>(r)),
  lensAsk: (id: string, body: LensAsk) =>
    fetch(`/api/runs/${id}/lens/ask`, { method: "POST", headers: JSON_HEADERS(), body: JSON.stringify(body) }).then((r) => json<LensTurn>(r)),
  lensPin: (id: string, turn: string, target: "report" | "dashboard") =>
    fetch(`/api/runs/${id}/lens/pins`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ turn, target }) }).then((r) => json<LensPin>(r)),
  lensSaveView: (id: string, turn: string) =>
    fetch(`/api/runs/${id}/lens/views`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ turn }) }).then((r) => json<LensView>(r)),
  lensRemoveView: (id: string) => fetch(`/api/runs/${id}/lens/views/short`, { method: "DELETE" }).then((r) => json<{ removed: string }>(r)),
  lensUnpin: (id: string, pin: string) => fetch(`/api/runs/${id}/lens/pins/${pin}`, { method: "DELETE" }).then((r) => json<{ removed: string }>(r)),
  openDemo: () => fetch("/api/demo", { method: "POST" }).then((r) => json<{ run_id: string; read_only: boolean }>(r)),
  removeDerived: (id: string, names: string[]) =>
    fetch(`/api/runs/${id}/remove-derived`, { method: "POST", headers: JSON_HEADERS(), body: JSON.stringify({ names }) }).then((r) => json<{ run_id: string }>(r)),
  getAnswersReview: (id: string) => fetch(`/api/runs/${id}/answers-review`).then((r) => json<{ workspace: string; status: string; answers: AnswerRow[] }>(r)),
  rerunWithAnswers: (id: string, answers: Record<string, string>, ask_again: string[]) =>
    fetch(`/api/runs/${id}/rerun-answers`, { method: "POST", headers: JSON_HEADERS(), body: JSON.stringify({ answers, ask_again }) }).then((r) => json<{ run_id: string }>(r)),
  getDashboardLayout: (id: string, importance: number[]) =>
    fetch(`/api/runs/${id}/dashboard/layout?importance=${importance.join(",")}`).then((r) => json<{ cols: number; rows: number; tiles: DashRect[] }>(r)),
  getDashboard: (id: string) => fetch(`/api/runs/${id}/dashboard`).then((r) => json<DashData>(r)),
  getSources: (id: string) => fetch(`/api/runs/${id}/sources`).then((r) => json<DataSource[]>(r)),
  downloadUrl: (id: string, source: string) => `/api/runs/${id}/data/download?source=${encodeURIComponent(source)}`,
  getData: (id: string, source: string, offset: number, limit: number, q: string, sort: string, desc: boolean) =>
    fetch(`/api/runs/${id}/data?source=${source}&offset=${offset}&limit=${limit}&q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sort)}&desc=${desc}`).then((r) => json<DataPage>(r)),
  getCompare: (id: string) => fetch(`/api/runs/${id}/data/compare`).then((r) => json<DataCompare>(r)),
  cleanedCsvUrl: (id: string) => `/api/runs/${id}/files/cleaned.csv`,
  reportMdUrl: (id: string) => `/api/runs/${id}/files/report.md`,
  powerbiUrl: (id: string) => `/api/runs/${id}/powerbi`,
  eventsUrl: (id: string) => `/api/runs/${id}/events`,
  getQuestions: (id: string) => fetch(`/api/runs/${id}/questions`).then((r) => json<QuestionSet>(r)),
  submitAnswers: (id: string, body: { answers: Record<string, string>; notes: string; definitions: Record<string, string>; skip: boolean }) =>
    fetch(`/api/runs/${id}/answers`, { method: "POST", headers: JSON_HEADERS(), body: JSON.stringify(body) }).then((r) => json<{ status: string }>(r)),
  listGlossary: () => fetch("/api/glossary").then((r) => json<{ workspaces: Record<string, GlossaryEntry> }>(r)),
  putGlossary: (workspace: string, entry: GlossaryEntry) =>
    fetch(`/api/glossary?workspace=${encodeURIComponent(workspace)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(entry) }).then((r) => json<GlossaryEntry>(r)),
  deleteGlossary: (workspace: string) => fetch(`/api/glossary?workspace=${encodeURIComponent(workspace)}`, { method: "DELETE" }).then((r) => json<{ deleted: string }>(r)),
};

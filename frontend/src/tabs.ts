/** The app's real tabs, with plain names (two or three words that say what the tab does). Shared by the results page and the start page tour. */
export type Tab = "dashboard" | "report" | "insights" | "strategy" | "data" | "model" | "cleaning" | "plan" | "powerbi" | "chat";
export const TAB_LABEL: Record<Tab, string> = {
  report: "Report",
  dashboard: "Dashboard",
  insights: "Insights",
  strategy: "Ideas to try",
  data: "Your data",
  model: "How files connect",
  cleaning: "What we cleaned",
  plan: "How we analysed",
  powerbi: "Power BI files",
  chat: "Lens",
};
export const TAB_LINE: Record<Tab, string> = {
  report: "A short report with the answer and what to do. Download it as a PDF or print it from the Export menu.",
  dashboard: "Click any bar or month to filter every chart at once.",
  insights: "Each finding, how far to trust it, and the actions it supports.",
  strategy: "Ideas to test, ranked by how much they could help and how hard they are.",
  data: "The cleaned table behind every number, and what each column means. Search it, sort it, download it. One of four views in the Data tab.",
  model: "How your files were linked together, and how well they matched. A view in the Data tab.",
  cleaning: "Every change made to your data, with the rows it touched. A view in the Data tab.",
  plan: "The questions we set out to answer, and the checks we ran. A view in the Data tab.",
  powerbi: "Everything you need to rebuild the charts in Power BI. Open it from the Export menu.",
  chat: "Ask Lens anything about your data. Expand opens the conversation full width.",
};

/** The five tabs of the analysis view, in order. The Data tab holds four sub-views (the former setup tabs); every item keeps its id and its backend tab (`Tab`), which is what Lens and the view edits use. */
export type NavTab = "report" | "insights" | "strategy" | "dashboard" | "data";
export const NAV_TABS: NavTab[] = ["report", "insights", "strategy", "dashboard", "data"];
export const NAV_LABEL: Record<NavTab, string> = { report: "Report", insights: "Insights", strategy: "Ideas to try", dashboard: "Dashboard", data: "Data" };
export type DataView = "data" | "model" | "cleaning" | "plan";
export const DATA_VIEWS: DataView[] = ["data", "model", "cleaning", "plan"];
export const DATA_LABEL: Record<DataView, string> = { data: "Your data", model: "How files connect", cleaning: "What we cleaned", plan: "How we analysed" };

/** Where an old tab name now lives: a tab, a Data sub-view, the Export menu (Power BI, Print) or the expanded Lens (Chat). Old links and old saved names use the names the app had before. */
export type Target = { nav: NavTab; data?: DataView; exportItem?: "powerbi" | "print" | "briefs" | "pdf"; lens?: boolean };
export function resolveTarget(name: string | null | undefined): Target | null {
  const k = String(name ?? "").toLowerCase().replace(/[^a-z]/g, "");
  const map: Record<string, Target> = {
    report: { nav: "report" }, insights: { nav: "insights" }, strategy: { nav: "strategy" }, ideastotry: { nav: "strategy" }, ideas: { nav: "strategy" }, dashboard: { nav: "dashboard" },
    data: { nav: "data", data: "data" }, yourdata: { nav: "data", data: "data" },
    model: { nav: "data", data: "model" }, howfilesconnect: { nav: "data", data: "model" }, files: { nav: "data", data: "model" },
    cleaning: { nav: "data", data: "cleaning" }, whatwecleaned: { nav: "data", data: "cleaning" }, cleaned: { nav: "data", data: "cleaning" },
    plan: { nav: "data", data: "plan" }, howweanalysed: { nav: "data", data: "plan" }, analysis: { nav: "data", data: "plan" },
    powerbi: { nav: "report", exportItem: "powerbi" }, powerbiexport: { nav: "report", exportItem: "powerbi" },
    print: { nav: "report", exportItem: "print" }, pdf: { nav: "report", exportItem: "pdf" }, briefs: { nav: "report", exportItem: "briefs" },
    chat: { nav: "report", lens: true },
  };
  return map[k] ?? null;
}
/** The backend tab (the id the view edits and Lens use) of what is on screen. */
export const backendTab = (nav: NavTab, data: DataView): Tab => (nav === "data" ? data : nav);

/** The app's real tabs, with plain names (two or three words that say what the tab does). Shared by the results page and the start page tour. */
export type Tab = "dashboard" | "report" | "insights" | "strategy" | "data" | "model" | "cleaning" | "plan" | "powerbi";
export const TAB_LABEL: Record<Tab, string> = {
  report: "Report",
  dashboard: "Dashboard",
  insights: "Insights",
  strategy: "Ideas to try",
  data: "Your data",
  model: "How files connect",
  cleaning: "What we cleaned",
  plan: "How we analysed",
  powerbi: "Power BI export",
};
export const TAB_LINE: Record<Tab, string> = {
  report: "A short report with the answer and what to do. Print it or save it as a PDF.",
  dashboard: "Click any bar or month to filter every chart at once.",
  insights: "Each finding, how far to trust it, and the actions it supports.",
  strategy: "Ideas to test, ranked by how much they could help and how hard they are.",
  data: "The cleaned table behind every number. Search it, sort it, download it.",
  model: "How your files were linked together, and how well they matched.",
  cleaning: "Every change made to your data, with the rows it touched.",
  plan: "The questions we set out to answer, and the checks we ran.",
  powerbi: "Everything you need to rebuild the charts in Power BI.",
};

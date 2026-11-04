/** How many charts "More charts" holds: the planned finding charts that exist, plus the live charts that are actually drawn. The "in full" table is a table, not a chart, and does not count. */
export function moreChartCount(findingCharts: number, liveDrawn: number): number {
  return Math.max(0, findingCharts) + Math.max(0, liveDrawn);
}

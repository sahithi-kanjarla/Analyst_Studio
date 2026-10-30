# Build the dashboard in Power BI Desktop

**Goal:** Which segment and region earn the most revenue, and is revenue growing?

## 1. Load the data (2 min)
1. Home > Get data > Text/CSV > `cleaned_data.csv` > Load. (Or `cleaned_data.parquet` via Get data > Parquet.)
2. Check column types against `data_dictionary.csv` (column `recommended_power_bi_type`). Hide ID columns.
3. Optional: rename the table to **Data** (the measures assume this name).

## 2. Add the measures (3 min)
Model view > New measure, then paste each line from `measures.dax` (one measure per entry).

## 3. Apply the theme
View > Themes > Browse for themes > `theme.json`.

## 4. Build the visuals (what the analysis found)
| Visual | Axis / Category | Values | Tip |
|---|---|---|---|
| KPI cards | - | Row Count, main revenue measure | Place at the top of the page. |
| Line chart | order_date (Month) | revenue | Turn on Analytics > Trend line. |
| Line chart with legend | order_date (Month) | revenue, legend = Segment | Show only the top few groups so the lines stay readable. |
| Histogram (column chart on binned field) | revenue (bins) | Row Count | Create bins via New group > Bin. |
| Clustered bar chart | Segment | Average/Rate of revenue | Sort descending; add a slicer on other categories. |
| Clustered bar chart | region | Average/Rate of revenue | Sort descending; add a slicer on other categories. |
| Matrix with conditional formatting | Rows: Segment; Columns: region | Average/Rate of revenue | Format > Cell elements > Background color > Gradient, to reproduce the heatmap. |
| Scatter chart | Two strongly related numeric columns | - | Add a trend line from the Analytics pane. |
| Scatter chart | discount_pct | revenue | Add a trend line from the Analytics pane; put a categorical column in Legend to check segments. |
| Column chart by weekday / month | order_date (Day of week or Month name) | revenue | Add the date hierarchy and drill to Day or Month; add an average line from Analytics. |
| Table | Segment | Rows, average, total | Use conditional formatting data bars on the value columns. |
| Table | Numeric columns | Rows, average, total | Use conditional formatting data bars on the value columns. |
| Line chart | order_date (Month) | revenue | Turn on Analytics > Trend line. |
| Clustered bar chart | Segment | Average/Rate of revenue | Sort descending; add a slicer on other categories. |
| Table | Segment | Rows, average, total | Use conditional formatting data bars on the value columns. |
| Clustered bar chart | region | Average/Rate of revenue | Sort descending; add a slicer on other categories. |
| Table | region | Rows, average, total | Use conditional formatting data bars on the value columns. |

## 5. Tell the story
Page title = the goal. Put KPI cards on top, then the trend, then the segment comparisons. Add slicers for the grouping columns used in analysis.
Use the written findings in `report.md` as the text boxes/captions for each visual.

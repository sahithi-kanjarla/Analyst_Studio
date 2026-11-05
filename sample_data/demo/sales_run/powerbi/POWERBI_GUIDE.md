# Build the dashboard in Power BI Desktop

**Goal:** Find what drives revenue and returns, and what to do about it.

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
| KPI cards | - | Row Count, main Product revenue measure | Place at the top of the page. |
| Histogram (column chart on binned field) | Product revenue (bins) | Row Count | Create bins via New group > Bin. |
| Column chart + line (Pareto) | category | Total Product revenue + cumulative % | Use 'Show value as > Running total %' for the line. |
| Line chart | Order Date (Month) | Product revenue | Turn on Analytics > Trend line. |
| Clustered bar chart | category | Average/Rate of Product revenue | Sort descending; add a slicer on other categories. |
| Line chart | Order Date (Month) | Product revenue | Turn on Analytics > Trend line. |
| Clustered bar chart | Sales Channel | Average/Rate of Product revenue | Sort descending; add a slicer on other categories. |
| Clustered bar chart | Payment Method | Average/Rate of Product revenue | Sort descending; add a slicer on other categories. |
| Clustered bar chart | region | Average/Rate of Product revenue | Sort descending; add a slicer on other categories. |
| Clustered bar chart | category | Average/Rate of Product revenue | Sort descending; add a slicer on other categories. |
| Clustered bar chart | category | Average/Rate of Product revenue | Sort descending; add a slicer on other categories. |

## Field names in Power BI
The visuals above use plain names. Power BI lists each column under the exact name in the file, shown here:

| Plain name | Field in Power BI |
|---|---|
| Unit Price | `unit_price` |
| Order Date | `order_date` |
| Sales Channel | `sales_channel` |
| Payment Method | `payment_method` |
| region | `region` |
| category | `category` |
| Unit Cost | `unit_cost` |
| Product revenue | `product_revenue` |
| Unit price to unit cost ratio | `unit_price_to_unit_cost_ratio` |

## 5. Tell the story
Page title = the goal. Put KPI cards on top, then the trend, then the segment comparisons. Add slicers for the grouping columns used in analysis.
Use the written findings in `report.md` as the text boxes/captions for each visual.

## Alternative: a proper star-schema model (recommended for reuse)
The flat `cleaned_data.csv` already combines your tables (one row per `order_lines`). If you prefer to model them natively in Power BI:
1. Get data > Text/CSV for every file in `tables/`.
2. Model view > Manage relationships > New, one per row of `relationships.csv` (many-to-one from `from_table.from_column` to `to_table.to_column`, single direction).
3. In `measures.dax`, replace the table name `'Data'` with your fact table (`order_lines`).

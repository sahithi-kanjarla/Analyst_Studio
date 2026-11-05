# Demo sample: several connected files

Made by `python scripts/make_sample_data.py` (seeded, same CSVs every run). Generic, invented data for a small online seller, Jan 2024 – Dec 2025.

| file | rows | key columns |
|---|---|---|
| `customers.csv` | 1,400 | `customer_id`, `region`, `acquisition_channel`, `signup_date` |
| `products.csv` | 70 | `sku`, `category`, `unit_cost`, `list_price` |
| `orders.csv` | ~3,300 | `order_id`, `customer_ref`, `order_date`, `sales_channel`, `payment_method` |
| `order_lines.xlsx` | ~5,900 | `line_id`, `order_id`, `item_code`, `quantity`, `unit_price` |
| `returns.csv` | ~490 | `return_id`, `line_id`, `reason`, `refund_amount` |

## How the files link (different names on purpose)
- `orders.customer_ref` → `customers.customer_id`
- `order_lines.order_id` → `orders.order_id`
- `order_lines.item_code` → `products.sku`
- `returns.line_id` → `order_lines.line_id`

## What is hidden in the data (check the report found it)
1. **Returns:** Apparel lines are returned about **26%** of the time; every other category is **3–5%**. Apparel reasons are mostly size-related ("Wrong size", "Did not fit").
2. **Trend:** order volume **grows about 2.6×** over the 24 months (with mild yearly seasonality).
3. **Top / bottom category by revenue:** **Kitchen** is top (~$111k), **Stationery** is bottom (~$8k). Apparel is third on revenue but worst on returns.
4. **Top / bottom region (via customers):** **North** is far ahead (~$135k); **Islands** is lowest (~$10k).

## Messiness (on purpose)
- **Mixed date format:** the last 160 rows of `orders.csv` use `DD/MM/YYYY`; the rest use `YYYY-MM-DD`.
- **Duplicates:** 9 exact duplicate rows in `orders.csv`, 18 in `order_lines.xlsx`.
- **Missing values:** ~30 `payment_method`, 12 `customer_ref` (orders); 40 `quantity` (lines); 70 `acquisition_channel` and 25 `region` (customers); 3 `unit_cost` (products); 15 return `reason`.
- Column names differ across files for the same key (see above); one file is Excel.

# Varied sales exports (synthetic)

Made by `scripts/make_varied_datasets.py` (deterministic, seeded). No real personal data: every name, e-mail, phone number and GSTIN is made up.
"documented" = the layout follows the platform's public documentation; "typical pattern" = an approximation of what such an export looks like in practice.

| file | what it imitates | layout |
|---|---|---|
| `a_shopify_orders.csv` | a Shopify orders export: one row per line item, order-level fields blank on the extra rows, 57 columns, dates with a time and a UTC offset | **documented** |
| `b_flipkart_payout.xlsx` | a Flipkart payout workbook: Help, Orders, Returns and Cancellations sheets; each fee column has a matching waiver column | **documented** |
| `c_amazon_settlement.txt` | an Amazon settlement flat file: tab-delimited, a summary row first, fees and refunds as separate negative rows | **typical pattern** |
| `d_tally_gst_sales_register.xlsx` | a Tally GST sales register: title rows above the header (one merged), a Grand Total row | **typical pattern** |
| `e_hindi_headers.csv` | headers in transliterated Hindi mixed with English | **typical pattern** |
| `f_wide_80_columns.csv` | an 80-column table | **typical pattern** |
| `g_mixed_dates_amounts.csv` | mixed date formats with a likely day/month swap; amounts like "₹1,25,000", "Rs. 12.5k", "1.25 lakh" | **typical pattern** |
| `h_quantities_no_money.csv` | quantities but no money column | **typical pattern** |
| `i_messy_headers.csv` | duplicate, blank and padded headers, empty columns, "-" and "N/A" as blanks, order ids in scientific notation, phone numbers that lost their leading zeros | **typical pattern** |

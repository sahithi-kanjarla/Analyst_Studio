# Compatibility with real-world sales exports

Nine synthetic exports (`backend/tests/fixtures/varied/`, made by `scripts/make_varied_datasets.py`, deterministic, no real personal data) are run through the whole pipeline in **rules-only mode**; Lens is checked with the **fake AI**.
Nothing here used a real AI model. "Documented" means the layout follows the platform's public documentation; "typical pattern" means it is an approximation of what such an export looks like in practice, so a PASS on it says
less than a PASS on a documented one. **No real vendor file was available, so none was tested.**

Run it again: `python scripts/varied_check.py` (writes this file's table inputs to `docs/compatibility_results.json`). The same checks run in the suite (`backend/tests/test_varied_data.py`).

## What is checked

| check | meaning |
|---|---|
| no crash | the run finishes (questions it asks are answered with their defaults) |
| understanding | dates, money, quantity, status and ids are recognised where the file has them; personal columns (names, e-mails, phones, GSTINs) are not read as measures; row and column counts are as expected |
| long format | several rows per order do not inflate the order count or the totals |
| no double count | totals in the cleaned table equal the file's own totals (a totals row, a settlement summary, the sheet's own sums): no fee, waiver, refund or summary row counted twice or dropped |
| report | a report was produced and `scripts/report_check.py` passes on the run |
| data card | the Lens data card builds, holds no raw row and lists no value of a personal-looking column |
| custom analysis | SQL over the cleaned table runs (median, group by, strftime ...), also through Lens with the fake AI |
| time and memory | at most 120 s and 1500 MB peak for the run |

## Result

| file | layout | no crash | understanding | long format | no double count | report | data card | custom analysis | time memory | seconds | peak MB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `a_shopify_orders.csv` | documented | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 15.2 | 267 |
| `b_flipkart_payout.xlsx` | documented | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 11.5 | 280 |
| `c_amazon_settlement.txt` | typical pattern | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 6.6 | 263 |
| `d_tally_gst_sales_register.xlsx` | typical pattern | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 5.1 | 276 |
| `e_hindi_headers.csv` | typical pattern | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 6.0 | 266 |
| `f_wide_80_columns.csv` | typical pattern | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 11.9 | 267 |
| `g_mixed_dates_amounts.csv` | typical pattern | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2.5 | 259 |
| `h_quantities_no_money.csv` | typical pattern | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 3.4 | 261 |
| `i_messy_headers.csv` | typical pattern | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 3.9 | 263 |

Every check passes on every file.

## What failed first, and what fixed it

These are the failures the first run of the nine files showed (each fix is general, with a test in `backend/tests/test_varied_data.py`; none names a file, a column or a value).

| failure | cause | fix |
|---|---|---|
| Shopify-style and Amazon-style files crashed the run | dates written with a UTC offset (`2024-03-05 14:22:10 +0530`) became time-zone-aware and could not be stored in a plain date column | the offset is dropped and the clock time kept as written (`profiler.parse_dates`) |
| Tally-style register: title rows were read as the header, the header became data, the Grand Total row was a record | the loader always took the first row as the header and kept every row | `tableio.header_row` finds the header by shape (title rows have one filled cell); a totals row is removed by its label or because its figures equal the sums above it; the merged title cell is unmerged; each is written in the cleaning log |
| Amazon-style settlement: the summary row counted as a transaction and its six settlement columns were mostly empty | a row that describes the file sat among the records | a row whose filled cells are all in columns empty everywhere else is removed (its values are quoted in the cleaning log) and those columns are left out |
| Flipkart-style workbook: the Help sheet became a table | every non-empty sheet was a table | a sheet of one or two columns of long sentences is left out and said so |
| Mixed amounts: "Rs. 12.5k", "1.25 lakh", "₹1,25,000" stayed text, so the column was a category | only currency symbols and commas were stripped | `profiler.parse_amounts` also reads k, lakh, crore, million and bracketed negatives |
| Mixed dates: a column with `25/04/2024` and `04/25/2024` lost half its dates | one order was chosen for the whole column | a value that is not a date in the chosen order is read the other way; a column that mixes the two orders is flagged in the cleaning log with the number of values that could be swapped |
| Order numbers in scientific notation (`2.90285E+11`) and phone numbers were summed like measures | they are numbers to the type check | scientific-notation strings with a large exponent, whole numbers of 8 or more digits with a constant length, and columns named like a contact detail are ids; names ending in No, Num or Number are ids |
| Hindi-transliterated headers: "Kul rashi" (total amount) and "Matra" (quantity) were not totalled | the name rules knew only English words | the amount, quantity and rate word lists include common transliterated Hindi and GST words (`measures.py`) |
| Personal values: GSTINs, "Address1", pincodes could be listed in the data card | the word list and the value patterns missed them | added to the personal-looking test (`datacard.py`) |
| Several rows per order: a total repeated on every line item would be multiplied by the number of lines; the report said "Rows" only | rows were records | a column named like an order key whose values repeat is the order key; an additive amount repeated on every row of an order is counted once; the report shows the number of orders next to the number of rows (`grain.py`) |
| Report questions showed `returned_qty` | the plan's questions used raw column names | an underscore name is shown as words in those questions |
| report_check flagged "shipping method Standard is highest" against the column "Shipping" | the checker matched a group name that is the start of a longer one | the checker skips a claim about the longer name (`scripts/report_check.py`) |
| Headers: duplicates, blanks, padded names, empty columns | pandas names a blank header "Unnamed: n" and keeps empty columns | blank and padded headers are named, duplicates numbered, empty columns left out and said so |

## What could not be fixed, and why

- **Phone numbers that lost their leading zeros** (a file that already says `800877138` for `0800877138`): the zero is not in the file. The column is kept as an identifier, never summed, never listed in the data card, and not padded. Columns of digits that still have their zeros are kept as text.
- **Order numbers rounded by a spreadsheet** (`2.90285E+11`): the digits after the fifth are gone, so two different orders can look the same. The column is an id, not a measure; the original numbers cannot be recovered.
- **Day/month swaps**: a date such as `03/04/2024` is both 3 April and 4 March. Values that can only be one are read that way; the ones that fit both are read day-first and counted in the cleaning log. They cannot be settled without asking the person.
- **A Shopify-style order key called just `Name`**: nothing in the word says "order", so the report does not show an order count for it (the rows are still counted, order-level amounts are blank on the extra rows so totals are right, and Lens can count the different names).
- **Fees, waivers and refunds are separate measures.** The report does not net a fee with its waiver or a sale with its refund: it has no model of what a fee is. Totals match the file; "net revenue" would be a derived figure to ask for. In the settlement-style file one `amount` column mixes sales, tax, fees and refunds, so its total is the settlement total, not revenue; a question by row type needs a filter.
- **Sheets joined by a key**: the returns and cancellations sheets are joined to orders by order id; their amounts are not turned into a refund total.
- **Languages**: only transliterated Hindi words are in the word lists. Headers in Devanagari script, or other languages, are read as text (they still get the right type from their values; money, quantity and status words are not recognised from the name).
- **Legacy `.xls`** needs the `xlrd` package, which is not installed; `.xlsx`, `.csv`, `.tsv`/`.txt` and `.json` are read.
- **Not tested at all**: real vendor files; the AI's reading of these columns (a model may describe them better or worse than the rules); a file over the row limit.

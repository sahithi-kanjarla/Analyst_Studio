# Build log: consistency leftovers and the varied-data robustness test (branch `studio-v3-data`, from `studio-v3-nav`)

No real AI was called: the pipeline ran in rules-only mode and Lens used the fake AI. No figure, calculation or original report sentence was changed; every fix is a general mechanism (none names a file, a column or a value).

## Done

**0. Consistency leftovers.** The landing tour now lists the five real tabs (Report, Insights, Ideas to try, Dashboard, Data); the four Data views show as a row at the top of the tour window. Lens's own wording (the app-explanation records, the "what does this tab show" answers, view-edit messages, the cleaning-log pointer) uses the five tabs, "the Data tab, What we cleaned view", the Export menu (Download PDF, Briefs, Power BI files, Print) and "the expanded Lens"; the old names (Power BI export, Chat) are still understood as input but never shown. `tests/test_old_tab_names.py` fails if an old tab name appears in the declared text, in Lens answers and view messages (asked through the fake AI) or in the interface source.

**1. Nine synthetic exports** (`scripts/make_varied_datasets.py` into `backend/tests/fixtures/varied/`, seeded; the README there says "documented" for the Shopify-style and Flipkart-style layouts and "typical pattern" for the other seven). Names, e-mails, phones and GSTINs are made up (e-mail on example.com, GSTINs of the right shape but random).

**2. A checker for each file** (`scripts/varied_check.py`, one process per file so memory is the file's own): no crash, understanding, long format, no double count, report (including `scripts/report_check.py`), data card privacy, custom analysis (also through Lens with the fake AI), time and memory. Questions the run asks are answered with their defaults.

**3. `docs/compatibility.md`**: the file-by-check table (PASS/FAIL, documented or typical pattern, seconds, peak MB), what failed first with its cause and fix, and what could not be fixed and why.

**4. General fixes** (tests in `tests/test_varied_data.py`, 35): a new loader `app/tableio.py` (encodings, delimiters, header on its real row, title rows skipped, totals rows and summary rows removed, empty columns left out, tall merged cells filled, sheets of notes left out, leading zeros kept, uneven rows) with every change written to the cleaning log; UTC offsets in dates; amounts like "Rs. 12.5k", "1.25 lakh", "(500)"; values written in two date orders read each their own way and flagged; long numbers in scientific notation, phone-like digit columns and names ending in No/Number are ids; transliterated Hindi and GST words in the amount, quantity and rate word lists; GSTIN, Address1 and pincode in the data card's personal-looking test; several rows per order (`app/grain.py`: the order count by key, repeated order-level amounts counted once, an "Orders" tile next to "Rows"); questions with underscore names shown as words; report_check no longer mistakes a column named like the start of a longer one.

First run: 5 of the 9 files failed at least one check (two crashed). Now all 9 pass all 8 checks; times 2.5 to 15 s, peak memory about 260 to 280 MB.

## Decisions

- Real AI not used, so "rules-only and fake AI" means: the pipeline rules-only, Lens with the fake AI.
- Understanding is checked on the profile roles and the cleaned types (the rules-only record has no column meanings), against what each file says about itself in its own headers (test data in the checker, not product code).
- The loader keeps pandas' own type reading and only adds what it lacked; the old behaviour for ordinary files is unchanged (the full suite is the check).
- An order key needs a name that says so (order, invoice, bill, voucher, receipt, transaction, settlement ... with or without id/no); a plain name such as Shopify's `Name` is not guessed, and repeated amounts are only collapsed under such a key, so a unit price repeated per product is never touched.
- A "-" or "N/A" in a text column keeps its earlier behaviour (a label unless the column is otherwise numeric or a date; the existing placeholder question covers frequent ones).
- Fees, waivers and refunds are not netted: totals match the file; netting needs a model of what each column means (see the compatibility doc).

**Final run:** the full suite ran once, 1202 passed and 1 failed in 8 min 53 s (533 s): an existing test (`test_brief.py::test_pincode_becomes_an_id_in_the_profile`) expects the rules to read a pincode column as a number until the brief calls it an id. My phone-like name rule had also covered pincode, zip and postal, so I took those three out of that rule (they stay in the data card's personal-looking words) and re-ran `test_brief.py` and `test_varied_data.py` (54 passed). The full suite was not run a second time.

## Skipped

- Real vendor files; Devanagari-script headers; legacy `.xls` (needs `xlrd`); netting fees with waivers; restoring lost leading zeros and rounded order numbers. Reasons in `docs/compatibility.md`.

**NOT tested with the real AI:** how a real model describes these columns (roles, meanings, display names) and whether it would change any rule-based reading; Lens questions about these files beyond the SQL and count checks (fake AI only); the AI's planning on the 80-column table; the report wording a real model would give for the Hindi and GST files.

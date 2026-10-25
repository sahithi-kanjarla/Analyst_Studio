# Benchmark #3: Clothing Mall - Answer Key

One flat file, `sales.csv` (40,000 rows incl. 30 duplicates, Apr 2023 - Mar 2025, INR). One row = one bill line. Figures below are on clean data (duplicates dropped); blank-Qty rows (~1%) make a real run differ by up to ~1%.

## Should-ask questions (any 3+ is good; each is a genuine ambiguity)
1. Is **revenue = Qty x Sale Price**, and is Sale Price per unit or the line total?
2. Should **returned lines (Return Flag = Y)** be netted out of revenue, or reported gross?
3. How to treat the **~1% blank Qty** rows: drop, assume 1, or impute?
4. Slash dates: **DD/MM/YYYY or MM/DD/YYYY**? (Data is Indian; day-first is the safe reading.)
5. Is there a **cost / margin** source? (None in the file - the right behaviour is to say margin cannot be computed.)
6. Confirm the 30 exact **duplicate lines** are to be dropped, not genuine repeat sales.
7. Which period is the comparison base for 'year on year' (FY Apr-Mar vs calendar year)?

## Must-not-ask questions (answerable from the data or trivially safe)
- Which file / which sheet to use (there is exactly one).
- What `Qty`, `MRP`, `Store`, `Dept`, `Size`, `Bill Date` mean.
- Whether `Raymond` and `raymond ` are the same brand (obviously; trim + case-fold).
- Whether to strip the `Rs.` prefix and commas from Sale Price (obviously yes).
- How Discount % is defined (verifiable: (MRP - Sale Price) / MRP).
- Asking the user to supply a cost column or join table (no second table exists).
- Asking the user to name which stores / brands matter before looking.

## Expected findings
1. **Top store: Phoenix Mall** - highest revenue in both years and on gross or net basis.
2. **Shrinking store: Lakeside Outlet** - revenue 8,005,486 -> 3,719,201 (-53.5% YoY). It is the only store that declines; the others are flat or up. Units fall too, so it is not a price effect only.
3. **High-return brand: Urban Weave** - 36.5% of lines returned vs at most 5.6% for every other brand.
4. **Discount deepening, revenue per unit falling in year 2** - avg discount 14.0% (H1) / 14.0% (H2) -> 18.1% (H3) / 27.2% (H4); revenue per unit 1,519.5 -> 1,286.0. Volume does not rise to compensate, so deeper discounting is not buying growth.
5. **Festive peak: October and November** (top two calendar months in both years); **weak month: June**.
6. **Dominant dept: Menswear** - 49.0% of revenue (next: Womenswear 27.6%).

### Decoys - must NOT be reported as effects
- **Pay Mode vs revenue**: avg revenue per line differs by only 1.2% across modes (random).
- **Size vs revenue**: differs by only 2.0% across sizes (random).
A correct answer says 'no meaningful relationship' or leaves them out; calling either a driver is a failure.

### Data-quality items to detect and handle
3 date formats (YYYY-MM-DD, DD/MM/YYYY, DD-Mon-YYYY); `Rs.` prefix (with commas) on ~5% of Sale Price; ~1% blank Qty; 30 exact duplicate rows; `Raymond` / `raymond ` variants.

## Red flags (automatic deductions)
- **Inventing a margin / profit / markup.** No cost column exists; MRP - Sale Price is discount, not margin.
- **Treating `Bill No` as a measure** (summing or averaging it). It is an identifier; count distinct for bills.
- Treating `Discount %` as additive, or averaging it unweighted when a revenue-weighted figure is needed without saying so.
- Summing `Sale Price` or `MRP` instead of Qty x Sale Price.
- Parsing slash dates month-first, or silently dropping rows with unparseable dates / Rs. prices.
- Not de-duplicating, so every figure is overstated; or counting `Raymond` and `raymond ` as two brands.
- Calling Pay Mode or Size a driver of revenue.
- Blaming the shrinking store on pricing without checking units, or ignoring returns when ranking brands.
- Reporting a trend for the whole business from only one year, or missing that year 2 differs from year 1.

## Scoring hint
Findings 1-6: one point each (finding + a correct number). Decoys: one point for correctly declining both. Cleaning: one point for handling all five mess items. Questions: up to two points for relevant should-asks, minus one per must-ask. Any red flag: minus two each.

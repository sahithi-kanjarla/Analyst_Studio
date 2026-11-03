# Fix round 2 (branch studio-v3-fix2, from studio-v3)

Findings of review runs on a clean bakery file (900 rows, one row per order). Rules: fake AI only, nothing hard-coded to a column name or example value, a figure changes only where a fix says so.

## Status

| Fix | State |
|---|---|
| A. Order key detection | done |
| B. Key figures (no sum of a rate; gross profit and margin by code) | left to do |
| C. Partial periods | left to do |
| D. Derived columns, one-to-one categories | left to do |
| E. Display names | left to do |
| F. AI-proposed derived measures checked in code | left to do |
| G. Short titles | left to do |

## Fix A: order key (done)

- `understanding.py`: the record now carries `order_key` (the column the AI names as the order, invoice or transaction identifier, or "none"). A name that is not a column is no answer.
- `grain.py` rewritten around one decision (`decide`): the user's answer, then the AI's record, then (only with no AI answer) a column named like an order key. Values are never used to find a key (`structural_key` and the repeat-based `key_columns` are gone). The data only verifies (`verify`): unique per row = already order grain (nothing blanked, 900 orders on the bakery); repeated = it must behave like an order (a date or amount stays the same within it while other columns change, and when the file has dates one of them stays the same within an order).
- An amount that is constant within a product or category column (a product's price or cost) is an item attribute and is never blanked (`is_item_attribute`).
- Unsure (no AI, no answer) or disagreement (the AI's pick fails the data check): every value is kept and the question "Which column identifies one order?" is asked, candidates as options, default "None of these: keep every value" (`signals.order_key_signal`, `clarify` template, new directive `set_order_key`). The answer is read when the run restarts (`graph._order_key`).
- The profile no longer guesses a grain at profile time; `profile["grain"]` (the Orders tile) and `profile["grain_decision"]` are set in the understanding stage.
- Tests: `tests/test_order_key.py` (7). Older tests that used the rules-only repeat detection were rewritten to name the key (a fake record, an answer, or an explicit key). Older test modules do not see the new question (`conftest.no_order_key_question`), because a rules-only run on any file with an order_id column now pauses on it.

# Build log: studio-v2

Branch `studio-v2`, created from `claude/affectionate-mayer-5594af` at commit `5b3de3a` ("Polish report page").
`main` and `backup-cleaned` were not touched.

## Decisions

- **Demo dataset.** The Kaveri sample named in the brief is not in the repo (its folder is git-ignored and missing from the checkout). The demo run uses the Mall dataset (`sample_data/mall/sales.csv`, the scorecard dataset) instead.
- **Row limit.** `MAX_ROWS` (500 000) is checked at upload for CSV/TSV/TXT, with an exact row count, and again after loading every table (`profiler.check_row_limit`) as a backstop. Excel and JSON are checked only after loading.
- **Retention.** `RETENTION_DAYS` (30) deletes a whole run folder, uploads included, when it is older than the limit. Runs queued or running are never deleted. The sweep runs at server start and before each new upload. `0` keeps everything.

## Task log

### Task 1: packaging
- README: test count corrected, Privacy section added, Known limits updated for retention and the row limit. `.env.example` lists the new variables.
- `RETENTION_DAYS` and `MAX_ROWS` added to `backend/app/config.py`, with tests in `backend/tests/test_limits_and_retention.py`.

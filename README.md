# ARCAIS Control Assessment Reconciliation

Reconciles a master control list (`data/controls.json`) with field
assessment results (`data/assessment_results.xlsx`) into a clean merged
dataset, surfaces everything that couldn't be cleanly reconciled, and
provides a small React app to review and edit the results.

See [`DATA_ISSUES.md`](./DATA_ISSUES.md) for the full writeup of every
anomaly found and how it was handled.

## Part A — Reconciliation script

Pure Python standard library, no third-party dependencies (including for
reading the `.xlsx` file — see `xlsx_reader.py`).

```bash
python3 reconcile.py --controls data/controls.json --assessments data/assessment_results.xlsx --outdir .
```

This prints a per-domain summary to stdout and writes:
- `reconciled.json` — one clean record per control (`id`, `domain`, `name`,
  `owner`, `status`, `completion`, `evidence`, `assessed_on`)
- `exceptions.json` — every record/field that could not be cleanly
  reconciled, each with a `type` and a human-readable `reason`

Run the (small, illustrative) test suite:

```bash
python3 -m unittest test_reconcile.py -v
```

## Part B — Review UI

A Vite + React app in `ui/`. It reads `reconciled.json` and
`exceptions.json` as static files from `ui/public/` (copies of the Part A
output, so the UI works standalone without a backend).

```bash
cd ui
npm install
npm run dev       # local dev server
# or
npm run build && npm run preview   # production build
```

Features:
- Controls grouped by domain, with a search box (id / name / owner / domain)
- Per-control status dropdown (Not Started / In Progress / Complete) and an
  editable evidence note — changes update the live summary bar immediately
- Overall + per-domain % complete, recomputed on every edit
- An **Exceptions** tab listing everything from `exceptions.json`
- Defensive data loading throughout: a malformed or missing JSON file shows
  an inline warning banner instead of crashing the app, and every field from
  the reconciled data is sanitized/defaulted before being rendered

If you re-run `reconcile.py` and want the UI to reflect the new output,
copy the two JSON files into `ui/public/` again:

```bash
cp reconciled.json exceptions.json ui/public/
```

## Repo layout

```
data/
  controls.json
  assessment_results.xlsx
xlsx_reader.py       # stdlib-only .xlsx parser
reconcile.py         # Part A entry point
test_reconcile.py    # unit tests for the normalization logic
reconciled.json       # Part A output (committed for convenience)
exceptions.json       # Part A output (committed for convenience)
DATA_ISSUES.md        # Part C report
TIME_LOG.md           # Part D time log
ui/                    # Part B React app
```

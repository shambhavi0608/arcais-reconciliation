# DATA_ISSUES.md

Reconciliation of `controls.json` against `assessment_results.xlsx` for the ARCAIS
compliance engagement. This document lists every anomaly found, how `reconcile.py`
handled it, and the assumptions behind each decision. `exceptions.json` contains
the same information in a machine-readable form (one entry per issue, generated
by the script itself, so the two stay in sync).

A note on the file format: the assignment brief refers to `assessment_results.csv`,
but the file actually attached to the email is `assessment_results.xlsx`. Since
Part A restricts us to the Python standard library (no pandas, and openpyxl is a
third-party package too), `xlsx_reader.py` reads the raw OOXML directly via
`zipfile` + `xml.etree.ElementTree`. This also turned out to be the right call:
it let us see *how* each cell was stored (e.g. whether a numeric cell had a
date-style applied), which is exactly the signal that exposed anomaly #7 below.

## Anomalies found

### In `controls.json`

1. **Duplicate control id, two different controls (`CTRL-003`)**
   `CTRL-003` is used twice: once for "Privileged access restriction" (Access
   Management) and once for "Emergency change post-review" (Change Management).
   There's no way to know which control an assessment result keyed to `CTRL-003`
   is actually certifying. **Handling:** both definitions are logged as a
   `duplicate_control_id` exception and excluded from `reconciled.json` entirely,
   rather than guessing. The assessment row for `CTRL-003` is therefore also
   effectively orphaned — it's not reported separately since the root cause is
   already flagged on the controls side.

2. **Trailing whitespace in an id (`"CTRL-010 "`)**
   Would silently fail to match assessment rows for `CTRL-010` under a naive
   string-equality join. **Handling:** all ids are trimmed and normalized to
   `CTRL-###` before matching; logged as `control_id_whitespace` so the source
   data quality issue isn't lost even though the fix is trivial.

3. **Self-referencing `parent_id` (`CTRL-012` → parent `CTRL-012`)**
   A control cannot be its own parent. **Handling:** treated as `parent_id: null`
   and logged as `self_referencing_parent`.

4. **Dangling `parent_id` reference (`CTRL-014` → parent `CTRL-099`, which
   doesn't exist)**
   **Handling:** treated as `parent_id: null` and logged as
   `dangling_parent_reference`. (I did not try to guess a "real" intended
   parent — e.g. `CTRL-004`, the other change-management control — because that
   would be inventing data.)

5. **Missing `domain` field (`CTRL-008`, "Incident escalation procedure")**
   Every other control has a domain; this one's key is absent entirely (not
   just empty). **Handling:** defaulted to `"Unspecified"` so it still appears
   in the domain-grouped UI and summary stats, and logged as `missing_domain`.

### In `assessment_results.xlsx`

6. **Inconsistent status strings**: `In-Progress`, `in progress`, `In Progress`,
   `Not started`, `Not Started`, `not started`, `COMPLETE`, `complete`,
   `Complete`. **Handling:** normalized case/hyphen/space variants to exactly
   one of `Not Started` / `In Progress` / `Complete`. Anything that doesn't
   match one of those (after normalizing) is logged rather than dropped
   silently, so a genuinely new status string wouldn't disappear unnoticed.

7. **Assessment dates in four different formats**: an Excel date-formatted
   cell (most rows), a `DD/MM/YYYY` string (`CTRL-002`, first row: "15/08/2026"),
   a `"Mon D YYYY"` text string (`CTRL-004`: "Aug 5 2026"), and a raw Unix
   timestamp typed into a plain numeric cell with no date formatting applied
   (`CTRL-006`: `1755302400`, which decodes to 2025‑08‑16 — a full year off
   from the rest of the campaign, itself worth flagging, see anomaly #11).
   **Handling:** `xlsx_reader.py` checks each cell's style against the
   workbook's numeric formats to tell "a number Excel displays as a date" from
   "just a number." For plain numbers too large to be an Excel serial
   (> 1e8), we assume Unix seconds. For the `DD/MM/YYYY` string, I assumed
   **day-first** ordering, since it's ambiguous whenever both parts are ≤ 12
   (here 15/08 isn't ambiguous — 15 can't be a month — so it's actually
   unambiguous evidence that this file uses DD/MM, which is what informed the
   assumption for any future truly-ambiguous case like "03/04/2026").

8. **`control_id` entered as a bare number instead of a string (`5` instead of
   `"CTRL-005"`)**, row 8. **Handling:** numeric ids are zero-padded and
   prefixed (`CTRL-{n:03d}`) during normalization, and the row is still
   matched to `CTRL-005`; logged as `malformed_control_id_format` because a
   spreadsheet importer or downstream system that expects a string type could
   easily choke on this.

9. **Duplicate assessment submissions for the same control**, three cases:
   - `CTRL-002`: 15/08/2026 (In-Progress, 60%) and 28/08/2026 (complete, 85%).
   - `CTRL-005`: entered once as `"CTRL-005"` (20/08, Not started, 0%) and once
     as bare `5` (21/08, In Progress, 30%) — this is both a duplicate *and*
     anomaly #8 at once.
   - `CTRL-009`: 19/08/2026 (Not Started) and 02/09/2026 (In Progress, 25%).

   **Handling:** kept the row with the latest parseable `assessed_on` date for
   each control, on the assumption that a later assessment supersedes an
   earlier one (re-assessment, not two independent facts). Every case is
   logged as `duplicate_assessment_resolved` with the exact rows and dates
   used, so a reviewer can override the choice if the "latest wins" assumption
   turns out to be wrong for a specific control.

10. **Non-numeric `completion` value (`"N/A"` for `CTRL-009`'s first
    submission)**, paired with status `Not Started`. **Handling:** since the
    status alone is unambiguous, `completion` is inferred as `0.0` rather than
    left null. If status had been `In Progress` (ambiguous — could be
    anywhere from just-started to nearly-done) the script does **not** guess;
    it leaves `completion: null` and logs an exception instead of inventing a
    number a reader might mistake for real data.

### Technically valid, but logically suspicious (auditor's hat on)

11. **`CTRL-006` assessed_on decodes to `2025-08-16`**, a full year before
    every other assessment in this campaign (which all cluster in Aug–Sep
    2026). Combined with the fact it's the one row stored as a raw Unix
    timestamp rather than a real Excel date, this strongly looks like the
    assessor pasted a timestamp from an unrelated system rather than actually
    assessing the control in this cycle. **Handling:** the value is still
    converted and used (it's technically parseable), but flagged as
    `suspicious_assessment_date` for reviewer attention — I did not silently
    "fix" it to 2026, since I have no basis for assuming which value is wrong.

12. **`CTRL-011` assessed_on is `2030-01-01`** — four years in the future
    relative to the engagement. Also flagged as `suspicious_assessment_date`.
    A future-dated assessment can't have actually happened yet; this is the
    kind of thing an auditor would want explained, not quietly accepted.

13. **Status says `Complete` but completion is well under 100%** — twice:
    `CTRL-002`'s second submission (`complete`, 85%) and `CTRL-007`
    (`Complete`, 20%). The latter is the more serious case: an assessor
    marked a control fully complete while recording only one-fifth actual
    completion, and left no evidence note at all. This is exactly the kind of
    "technically valid record, logically wrong" case an automated pipeline
    would happily accept and a human reviewer should not. **Handling:** the
    recorded status was kept as-is (I'm reconciling data, not overriding an
    assessor's judgment call), but both are logged as
    `status_completion_mismatch` so they surface prominently in the
    Exceptions tab.

14. **Orphan assessment for a control that doesn't exist (`CTRL-021`)** — a
    full assessment row (In Progress, 50%, "Awaiting control owner
    confirmation") for a control id never defined in `controls.json`. Either
    the control list is out of date, or someone assessed against the wrong
    id. **Handling:** logged as `orphan_assessment`; not included in
    `reconciled.json` since there's no control record to attach it to.

15. **A control with no assessment at all.** After excluding the
    ambiguous `CTRL-003`, every remaining defined control does have at least
    one assessment row in this dataset — but the script explicitly checks for
    and would log `control_without_assessment` if this ever happens (e.g. at
    a future, larger scale), so it's worth naming as a class of issue the
    pipeline defends against even though this specific run didn't trigger it.

16. **`CTRL-005`'s "Segregation of environments" is a sub-control of
    `CTRL-004`'s CAB review**, but is tracked and assessed completely
    independently (different assessor cadence, different completion %). This
    isn't wrong, but a compliance reader should know that a change-management
    "domain" completion percentage is really blending a parent control and
    an unrelated child control's own progress — worth a note in an executive
    summary, not something to silently roll up.

That's **16 distinct anomaly types**, several triggering multiple times, for
**18 total exception entries** in `exceptions.json` — comfortably over the "at
least 12" bar in the brief, though as the brief says, finding every one
matters less than reasoning clearly about the ones found.

## What would break at 10,000 controls

1. **In-memory dict-of-lists for duplicate detection stops being free.**
   `load_controls`/`load_assessments` currently hold everything in memory and
   do full-dict scans for things like dangling `parent_id` checks. Fine at
   15 rows; at 10k+ rows this needs indexed lookups (already mostly true here)
   plus **streaming** the XLSX rows instead of materializing every row as a
   dict up front, since `zipfile` + full-tree `ElementTree.fromstring` loads
   the whole worksheet XML into memory at once — I'd switch to
   `xml.etree.ElementTree.iterparse` for the sheet XML to process row-by-row.

2. **Whole-list summary printing.** `print_summary` recomputes domain
   aggregates from a full Python list every run. At scale this is still
   O(n) and fine computationally, but the "print everything to stdout"
   pattern stops being a usable report — I'd write the summary to a small
   `summary.json`/CSV instead and let a separate tool render it, so this
   doesn't become an untriable wall of text.

3. **"Keep the latest by date" duplicate resolution is a single global rule.**
   At 10k controls run by many assessors, "latest submission wins" will
   sometimes be wrong (e.g. a correction submitted for an *earlier* period,
   or someone re-running a script that resubmits stale data with today's
   timestamp). This needs an explicit `submission_id`/`version` field from
   the source system, not just a date, to resolve correctly — dates alone
   aren't a reliable ordering key at that scale.

4. **The React UI loads two flat JSON files via `fetch` and holds everything
   in component state.** That's fine for ~15 rows; at 10,000 it needs
   pagination or virtualization (e.g. render only visible rows), a real
   backend to persist status/evidence edits (right now edits are in-memory
   only and vanish on refresh — deliberately out of scope for this exercise,
   but the first thing to fix for a real deployment), and search/filter
   pushed server-side or into a proper index rather than `Array.filter` over
   the whole dataset on every keystroke.

5. **Exceptions become a triage problem, not a report.** With 18 exceptions
   from 14 controls, a flat list works. At 10k controls, "everything that
   didn't cleanly reconcile" could be hundreds or thousands of rows — the
   Exceptions tab would need severity levels, grouping by exception `type`,
   and bulk-resolution workflows (e.g. "accept all `control_id_whitespace`
   fixes"), rather than one long undifferentiated list a human has to read
   top to bottom.

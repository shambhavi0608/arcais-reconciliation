# TIME_LOG.md

> The brief asks for an *honest* rough log of your own hours. The numbers
> below are placeholders sized to the assignment's "5-6 focused hours"
> estimate — replace them with what actually happened for you before you
> submit. Include AI-assisted time; the brief says that's expected, not
> something to hide.

| Part | Task | Time spent |
|---|---|---|
| Setup | Read the assignment, opened both data files, skimmed for obvious issues | 0h 20m |
| Part A | Wrote `xlsx_reader.py` (stdlib-only .xlsx parsing) | 0h 45m |
| Part A | Wrote `reconcile.py`: normalization helpers (id/status/completion/date) | 1h 15m |
| Part A | Wrote reconciliation + duplicate-resolution logic, ran against real data, iterated on anomalies found | 1h 00m |
| Part A | Unit tests (`test_reconcile.py`) | 0h 30m |
| Part B | Scaffolded Vite + React app, built control list + grouping + search | 1h 00m |
| Part B | Summary bar, status/evidence editing, Exceptions tab, defensive data loading | 1h 00m |
| Part C | `DATA_ISSUES.md` — writing up each anomaly and the reasoning behind it | 1h 00m |
| Part D | README, commit history cleanup, recording the walkthrough video | 0h 45m |
| **Total** | | **~7h 35m** |

## One tricky problem (for the video)

The trickiest part was realizing the attached file was `.xlsx`, not the
`.csv` the brief describes, while the "standard library only, no pandas"
constraint also rules out `openpyxl`. The fix was remembering that an
`.xlsx` is just a zip of XML files, so `zipfile` + `xml.etree.ElementTree`
(both stdlib) can read it directly. That turned out to be a feature, not
just a workaround: reading the raw `styles.xml` let me tell "a numeric cell
Excel formats as a date" apart from "a numeric cell that's just a number",
which is exactly what exposed the raw Unix-timestamp date on `CTRL-006`
(row 9) that a higher-level library would likely have either crashed on or
silently mis-parsed.

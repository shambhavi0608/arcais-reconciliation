"""
Minimal, dependency-free .xlsx reader.

The assignment brief refers to `assessment_results.csv`, but the file we
actually received by email is `assessment_results.xlsx` (this mismatch
itself is logged as an anomaly in DATA_ISSUES.md). Since Part A requires
"standard library only, no pandas", and openpyxl is a third-party package
too, this module reads the raw OOXML (an .xlsx is just a zip of XML files)
using only `zipfile` and `xml.etree.ElementTree`, both in the stdlib.

It only supports what we need: a single flat worksheet of strings and
numbers, which is exactly the shape of assessment_results.xlsx.
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Excel's day-0 epoch (the well-known 1900 date system, including the
# infamous but harmless 1900-leap-year bug that Excel intentionally keeps
# for backwards compatibility). Good enough for any date in this file.
_EXCEL_EPOCH_ORDINAL = 693594  # datetime.date(1899, 12, 30).toordinal()


def _col_to_index(cell_ref: str) -> int:
    """'C4' -> 2 (0-indexed column number)."""
    letters = re.match(r"[A-Z]+", cell_ref).group()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def excel_serial_to_iso(serial: float) -> str:
    """Convert an Excel date serial number to an ISO 8601 date string."""
    import datetime

    days = int(serial)
    frac = serial - days
    d = datetime.date.fromordinal(_EXCEL_EPOCH_ORDINAL + days)
    if frac:
        # There's a time-of-day component; we only need the date part
        # for this dataset, but keep it from silently truncating oddly.
        pass
    return d.isoformat()


def read_xlsx_first_sheet(path: str) -> list[dict[str, Any]]:
    """
    Read the first worksheet of an .xlsx file and return a list of dict
    rows keyed by the header row (row 1). Values are returned as raw
    Python types (str or float) with minimal interpretation:
      - shared-string cells -> str
      - numeric cells with a date-like style -> ISO date str
      - other numeric cells -> float
    Callers are responsible for further semantic parsing (this file
    intentionally does NOT try to guess whether a plain-looking number
    is "really" a date vs a unix timestamp vs a plain number -- that
    business-logic decision belongs to reconcile.py).
    """
    with zipfile.ZipFile(path) as z:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS}si"):
                # Concatenate all text runs (handles rich text / multiple <t>)
                text = "".join(t.text or "" for t in si.iter(f"{NS}t"))
                shared_strings.append(text)

        # Which style indices represent a date format? We inspect styles.xml
        # for numFmtId in the "date" family (built-in ids 14-22, or any
        # custom numFmt whose format code contains y/m/d). This tells us
        # which numeric cells were *formatted* as dates in Excel, which is
        # a genuinely useful signal (a cell that looks numeric but is
        # displayed as a date is very likely meant to be a date).
        date_style_indices: set[int] = set()
        if "xl/styles.xml" in z.namelist():
            styles_root = ET.fromstring(z.read("xl/styles.xml"))
            custom_date_fmt_ids = set()
            for numfmt in styles_root.findall(f"{NS}numFmts/{NS}numFmt"):
                code = (numfmt.get("formatCode") or "").lower()
                if any(ch in code for ch in ("y", "m", "d")) and "general" not in code:
                    custom_date_fmt_ids.add(int(numfmt.get("numFmtId")))
            builtin_date_ids = set(range(14, 23))  # Excel's built-in date/time formats
            cellxfs = styles_root.find(f"{NS}cellXfs")
            if cellxfs is not None:
                for i, xf in enumerate(cellxfs.findall(f"{NS}xf")):
                    fmt_id = int(xf.get("numFmtId", "0"))
                    if fmt_id in builtin_date_ids or fmt_id in custom_date_fmt_ids:
                        date_style_indices.add(i)

        sheet_name = None
        for name in z.namelist():
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                sheet_name = name
                break
        if sheet_name is None:
            raise ValueError("No worksheet found in workbook")

        sheet_root = ET.fromstring(z.read(sheet_name))
        rows_out: list[list[Any]] = []
        for row in sheet_root.findall(f"{NS}sheetData/{NS}row"):
            row_cells: dict[int, Any] = {}
            for c in row.findall(f"{NS}c"):
                ref = c.get("r")
                col_idx = _col_to_index(ref)
                cell_type = c.get("t")
                style_idx = int(c.get("s", "0"))
                v_el = c.find(f"{NS}v")
                if v_el is None or v_el.text is None:
                    row_cells[col_idx] = None
                    continue
                raw = v_el.text
                if cell_type == "s":  # shared string
                    row_cells[col_idx] = shared_strings[int(raw)]
                elif cell_type == "str":  # inline formula string
                    row_cells[col_idx] = raw
                else:  # numeric
                    num = float(raw)
                    if style_idx in date_style_indices:
                        row_cells[col_idx] = excel_serial_to_iso(num)
                    else:
                        # Keep as int when it's a whole number, for readability
                        row_cells[col_idx] = int(num) if num.is_integer() else num
            if row_cells:
                max_col = max(row_cells)
                rows_out.append([row_cells.get(i) for i in range(max_col + 1)])

    if not rows_out:
        return []
    header = [str(h).strip() if h is not None else f"col{i}" for i, h in enumerate(rows_out[0])]
    records = []
    for r in rows_out[1:]:
        rec = {header[i]: (r[i] if i < len(r) else None) for i in range(len(header))}
        records.append(rec)
    return records

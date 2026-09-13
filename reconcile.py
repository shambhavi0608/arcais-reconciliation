#!/usr/bin/env python3
"""
ARCAIS Control Assessment Reconciliation -- Part A

Reconciles controls.json (master control list) against
assessment_results.xlsx (field assessment results) into:
  - reconciled.json : one clean record per control
  - exceptions.json : everything that could not be cleanly reconciled,
                       with a human-readable reason

Standard library only. See DATA_ISSUES.md for the full writeup of every
anomaly this script had to handle and the assumptions made.

Usage:
    python3 reconcile.py [--controls PATH] [--assessments PATH] [--outdir DIR]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, date, timezone

from xlsx_reader import read_xlsx_first_sheet

VALID_STATUSES = ("Not Started", "In Progress", "Complete")

# Any assessed_on date after this is treated as "in the future relative to
# the engagement" and flagged as logically suspicious rather than silently
# trusted. The engagement's field work runs through early Sept 2026 in this
# dataset, so anything materially past that is worth a human's attention.
ENGAGEMENT_CUTOFF = date(2026, 9, 30)
ENGAGEMENT_START = date(2026, 1, 1)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------

def normalize_control_id(raw) -> str | None:
    """
    Coerce a control id into the canonical 'CTRL-###' form.
    Handles: surrounding whitespace, and assessment rows where the id was
    entered as a bare number (5 -> 'CTRL-005').
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return f"CTRL-{int(raw):03d}"
    s = str(raw).strip()
    m = re.match(r"^CTRL-?(\d+)$", s, re.IGNORECASE)
    if m:
        return f"CTRL-{int(m.group(1)):03d}"
    return s or None


def normalize_status(raw) -> tuple[str | None, str | None]:
    """
    Returns (normalized_status, warning). Handles casing and hyphen/space
    variants like 'In-Progress', 'in progress', 'COMPLETE', 'Not started'.
    """
    if raw is None:
        return None, "missing status"
    s = re.sub(r"[\s_-]+", " ", str(raw).strip()).lower()
    mapping = {
        "not started": "Not Started",
        "in progress": "In Progress",
        "complete": "Complete",
        "completed": "Complete",
    }
    norm = mapping.get(s)
    if norm is None:
        return None, f"unrecognized status value: {raw!r}"
    return norm, None


def normalize_completion(raw, status: str | None) -> tuple[float | None, str | None]:
    """
    Returns (completion_0_to_1, warning).
    'N/A' and other non-numeric completion values are treated as missing;
    if the status is unambiguous (Not Started / Complete) we can safely
    infer 0.0 / 1.0, otherwise we leave it None and raise it as a real
    exception rather than guessing.
    """
    if raw is None or (isinstance(raw, str) and raw.strip().upper() in ("N/A", "NA", "")):
        if status == "Not Started":
            return 0.0, "completion was missing/N-A; inferred 0.0 from status=Not Started"
        if status == "Complete":
            return 1.0, "completion was missing/N-A; inferred 1.0 from status=Complete"
        return None, "completion value missing/non-numeric and could not be inferred from status"
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None, f"completion value not numeric: {raw!r}"
    if val > 1:  # tolerate accidental percentages entered as e.g. 70 instead of 0.7
        if val <= 100:
            return val / 100.0, f"completion {raw!r} looked like a percentage; divided by 100"
        return None, f"completion value out of range: {raw!r}"
    if val < 0:
        return None, f"completion value negative: {raw!r}"
    return val, None


def normalize_date(raw) -> tuple[str | None, str | None]:
    """
    Returns (iso_date_str, warning). Handles:
      - already-ISO strings (from the xlsx reader's date-styled cells)
      - 'DD/MM/YYYY' strings (assumed DD/MM per company convention, see
        DATA_ISSUES.md -- this is ambiguous and explicitly called out)
      - 'Mon D YYYY' strings e.g. 'Aug 5 2026'
      - raw unix-epoch seconds stored as a plain number (not an Excel
        date serial) -- these show up in the 40000-60000 serial range vs
        1e9+ range for unix timestamps, so we can disambiguate by magnitude
    """
    if raw is None:
        return None, "missing assessed_on date"

    # Already normalized to ISO by xlsx_reader (date-styled numeric cell)
    if isinstance(raw, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw, None

    # Plain number that slipped through without a date style: could be a
    # unix timestamp typed in by mistake.
    if isinstance(raw, (int, float)):
        if raw > 1e8:  # unix seconds since 1970 -> way bigger than any excel serial
            try:
                d = datetime.fromtimestamp(raw, tz=timezone.utc).date()
                return d.isoformat(), (
                    "assessed_on was a raw unix timestamp (not an Excel date), "
                    "not a normal Excel date serial; converted assuming UTC seconds"
                )
            except (ValueError, OSError):
                return None, f"unparseable numeric date: {raw!r}"
        return None, f"unrecognized numeric date value: {raw!r}"

    s = str(raw).strip()

    # DD/MM/YYYY or D/M/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Assume DD/MM/YYYY (see DATA_ISSUES.md): only day can exceed 12 so
        # if the first number is >12 it must be the day either way; if
        # both are <=12 it's genuinely ambiguous and we go with the
        # documented DD/MM assumption.
        day, month = a, b
        if day > 12 and month > 12:
            return None, f"ambiguous/invalid date: {raw!r}"
        try:
            d = date(y, month, day)
            return d.isoformat(), "date given as D/M/Y; assumed DD/MM/YYYY (ambiguous format)"
        except ValueError:
            return None, f"invalid calendar date: {raw!r}"

    # 'Aug 5 2026' / 'August 5 2026' / 'Aug 5, 2026'
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mon_raw, day_raw, y_raw = m.groups()
        mon = MONTHS.get(mon_raw[:3].lower())
        if mon:
            try:
                d = date(int(y_raw), mon, int(day_raw))
                return d.isoformat(), "date given in 'Mon D YYYY' text format; parsed directly"
            except ValueError:
                return None, f"invalid calendar date: {raw!r}"

    return None, f"unrecognized date format: {raw!r}"


def flag_future_date(iso_date: str | None) -> str | None:
    if not iso_date:
        return None
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return None
    if d > ENGAGEMENT_CUTOFF:
        return f"assessed_on ({iso_date}) is after the engagement window ({ENGAGEMENT_CUTOFF.isoformat()}) -- looks like a data-entry error, review before trusting"
    if d < ENGAGEMENT_START:
        return f"assessed_on ({iso_date}) is before the engagement window started ({ENGAGEMENT_START.isoformat()}) -- likely a wrong-year timestamp (e.g. unix time converted off by a year), review before trusting"
    return None


# --------------------------------------------------------------------------
# Load & clean controls.json
# --------------------------------------------------------------------------

def load_controls(path: str, exceptions: list[dict]) -> dict[str, list[dict]]:
    """
    Returns a dict of normalized_id -> [control_dict, ...] (a list because
    controls.json has a genuine duplicate-id collision we must preserve
    and flag rather than silently overwrite).
    """
    with open(path) as f:
        raw_controls = json.load(f)

    by_id: dict[str, list[dict]] = defaultdict(list)
    seen_raw_ids = set()

    for entry in raw_controls:
        raw_id = entry.get("id")
        norm_id = normalize_control_id(raw_id)
        if raw_id != norm_id and isinstance(raw_id, str) and raw_id.strip() == norm_id:
            exceptions.append({
                "type": "control_id_whitespace",
                "control_id": norm_id,
                "reason": f"controls.json id {raw_id!r} had leading/trailing whitespace; normalized to {norm_id!r}",
            })

        domain = entry.get("domain")
        if not domain:
            exceptions.append({
                "type": "missing_domain",
                "control_id": norm_id,
                "reason": f"control {norm_id} ('{entry.get('name')}') has no 'domain' field in controls.json; left as 'Unspecified'",
            })
            domain = "Unspecified"

        cleaned = {
            "id": norm_id,
            "domain": domain,
            "name": entry.get("name"),
            "description": entry.get("description"),
            "owner": entry.get("owner"),
            "parent_id": normalize_control_id(entry.get("parent_id")) if entry.get("parent_id") else None,
        }
        by_id[norm_id].append(cleaned)
        seen_raw_ids.add(norm_id)

    # Flag duplicate control ids (two distinct controls sharing one id)
    for cid, entries in by_id.items():
        if len(entries) > 1:
            names = [e["name"] for e in entries]
            exceptions.append({
                "type": "duplicate_control_id",
                "control_id": cid,
                "reason": (
                    f"{cid} is used for {len(entries)} different controls in controls.json: "
                    f"{names}. Cannot determine which definition an assessment result for "
                    f"{cid} is meant to certify -- excluded from reconciled.json."
                ),
            })

    # Flag parent_id problems: self-reference and dangling reference
    all_ids = set(by_id.keys())
    for cid, entries in by_id.items():
        for e in entries:
            pid = e["parent_id"]
            if pid is None:
                continue
            if pid == cid:
                exceptions.append({
                    "type": "self_referencing_parent",
                    "control_id": cid,
                    "reason": f"{cid} lists itself as its own parent_id; treated as parent_id=null",
                })
                e["parent_id"] = None
            elif pid not in all_ids:
                exceptions.append({
                    "type": "dangling_parent_reference",
                    "control_id": cid,
                    "reason": f"{cid} has parent_id={pid!r} which does not exist in controls.json; treated as parent_id=null",
                })
                e["parent_id"] = None

    return by_id


# --------------------------------------------------------------------------
# Load & clean assessment_results.xlsx
# --------------------------------------------------------------------------

def load_assessments(path: str, exceptions: list[dict]) -> dict[str, list[dict]]:
    """Returns normalized_control_id -> [assessment_dict, ...] (all rows, unresolved)."""
    rows = read_xlsx_first_sheet(path)
    by_id: dict[str, list[dict]] = defaultdict(list)

    for i, row in enumerate(rows, start=2):  # row 1 is the header, data starts at row 2
        raw_id = row.get("control_id")
        norm_id = normalize_control_id(raw_id)
        if norm_id is None:
            exceptions.append({
                "type": "unparseable_assessment_row",
                "control_id": None,
                "reason": f"assessment_results.xlsx row {i}: control_id {raw_id!r} could not be parsed; row dropped",
            })
            continue
        if isinstance(raw_id, (int, float)):
            exceptions.append({
                "type": "malformed_control_id_format",
                "control_id": norm_id,
                "reason": (
                    f"assessment_results.xlsx row {i}: control_id was entered as bare number "
                    f"{raw_id!r} instead of '{norm_id}'; normalized automatically"
                ),
            })

        status, status_warn = normalize_status(row.get("status"))
        completion, completion_warn = normalize_completion(row.get("completion"), status)
        assessed_on, date_warn = normalize_date(row.get("assessed_on"))

        if status_warn:
            exceptions.append({"type": "status_normalization", "control_id": norm_id, "reason": f"row {i}: {status_warn}"})
        if completion_warn:
            exceptions.append({"type": "completion_normalization", "control_id": norm_id, "reason": f"row {i}: {completion_warn}"})
        if date_warn:
            exceptions.append({"type": "date_normalization", "control_id": norm_id, "reason": f"row {i}: {date_warn}"})

        date_sanity_warn = flag_future_date(assessed_on)
        if date_sanity_warn:
            exceptions.append({"type": "suspicious_assessment_date", "control_id": norm_id, "reason": f"row {i}: {date_sanity_warn}"})

        if status == "Complete" and completion is not None and completion < 0.99:
            exceptions.append({
                "type": "status_completion_mismatch",
                "control_id": norm_id,
                "reason": (
                    f"row {i}: status is 'Complete' but completion is {completion} "
                    f"-- logically inconsistent, flagged for reviewer (kept status as recorded)"
                ),
            })

        by_id[norm_id].append({
            "control_id": norm_id,
            "status": status,
            "completion": completion,
            "evidence": row.get("evidence_note"),
            "assessed_on": assessed_on,
            "assessor": row.get("assessor"),
            "_row": i,
        })

    return by_id


def pick_latest(assessments: list[dict], exceptions: list[dict]) -> dict:
    """Given multiple assessment rows for one control, keep the one with
    the latest parseable assessed_on date. Logs the decision as an
    exception entry so it's auditable, per the assignment's example."""
    dated = [a for a in assessments if a["assessed_on"]]
    undated = [a for a in assessments if not a["assessed_on"]]
    if not dated:
        # Nothing to sort by; just take the last row in file order and flag it
        chosen = assessments[-1]
        exceptions.append({
            "type": "duplicate_assessment_no_date",
            "control_id": chosen["control_id"],
            "reason": (
                f"{chosen['control_id']} has {len(assessments)} assessment rows, none with a "
                f"usable date; kept the last row in file order (row {chosen['_row']}) as a fallback"
            ),
        })
        return chosen
    dated.sort(key=lambda a: a["assessed_on"])
    chosen = dated[-1]
    if len(assessments) > 1:
        exceptions.append({
            "type": "duplicate_assessment_resolved",
            "control_id": chosen["control_id"],
            "reason": (
                f"{chosen['control_id']} had {len(assessments)} assessment submissions "
                f"(rows {[a['_row'] for a in assessments]}, dates "
                f"{[a['assessed_on'] for a in assessments]}); kept the latest by assessed_on "
                f"(row {chosen['_row']}, {chosen['assessed_on']})"
            ),
        })
    if undated:
        exceptions.append({
            "type": "duplicate_assessment_undated_ignored",
            "control_id": chosen["control_id"],
            "reason": f"{chosen['control_id']} also had {len(undated)} row(s) with no usable date, ignored in favor of the dated one",
        })
    return chosen


# --------------------------------------------------------------------------
# Reconcile
# --------------------------------------------------------------------------

def reconcile(controls_path: str, assessments_path: str):
    exceptions: list[dict] = []
    controls_by_id = load_controls(controls_path, exceptions)
    assessments_by_id = load_assessments(assessments_path, exceptions)

    reconciled = []

    for cid, entries in sorted(controls_by_id.items()):
        if len(entries) != 1:
            # Ambiguous duplicate control id -- already logged above, skip
            continue
        control = entries[0]
        assess_rows = assessments_by_id.get(cid)
        if not assess_rows:
            exceptions.append({
                "type": "control_without_assessment",
                "control_id": cid,
                "reason": f"{cid} ('{control['name']}') exists in controls.json but has no assessment result in assessment_results.xlsx",
            })
            reconciled.append({
                "id": cid,
                "domain": control["domain"],
                "name": control["name"],
                "owner": control["owner"],
                "status": None,
                "completion": None,
                "evidence": None,
                "assessed_on": None,
            })
            continue

        latest = pick_latest(assess_rows, exceptions)
        reconciled.append({
            "id": cid,
            "domain": control["domain"],
            "name": control["name"],
            "owner": control["owner"],
            "status": latest["status"],
            "completion": latest["completion"],
            "evidence": latest["evidence"],
            "assessed_on": latest["assessed_on"],
        })

    # Assessment rows referencing a control id that doesn't exist at all
    known_ids = set(controls_by_id.keys())
    for cid, rows in assessments_by_id.items():
        if cid not in known_ids:
            exceptions.append({
                "type": "orphan_assessment",
                "control_id": cid,
                "reason": f"assessment_results.xlsx has {len(rows)} row(s) for {cid}, which does not exist in controls.json",
            })

    return reconciled, exceptions


def print_summary(reconciled: list[dict], exceptions: list[dict]) -> None:
    per_domain_total: dict[str, int] = defaultdict(int)
    per_domain_completion: dict[str, list[float]] = defaultdict(list)

    for r in reconciled:
        per_domain_total[r["domain"]] += 1
        if r["completion"] is not None:
            per_domain_completion[r["domain"]].append(r["completion"])

    print("=" * 60)
    print("RECONCILIATION SUMMARY")
    print("=" * 60)
    for domain in sorted(per_domain_total):
        count = per_domain_total[domain]
        comps = per_domain_completion[domain]
        pct = (sum(comps) / len(comps) * 100) if comps else 0.0
        print(f"  {domain:<25} controls={count:<3} avg_completion={pct:5.1f}%")
    print("-" * 60)
    print(f"  Total controls reconciled : {len(reconciled)}")
    print(f"  Total exceptions logged   : {len(exceptions)}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Reconcile controls.json with assessment_results.xlsx")
    parser.add_argument("--controls", default="data/controls.json")
    parser.add_argument("--assessments", default="data/assessment_results.xlsx")
    parser.add_argument("--outdir", default=".")
    args = parser.parse_args()

    reconciled, exceptions = reconcile(args.controls, args.assessments)

    with open(f"{args.outdir}/reconciled.json", "w") as f:
        json.dump(reconciled, f, indent=2)
    with open(f"{args.outdir}/exceptions.json", "w") as f:
        json.dump(exceptions, f, indent=2)

    print_summary(reconciled, exceptions)
    print(f"\nWrote {args.outdir}/reconciled.json and {args.outdir}/exceptions.json")


if __name__ == "__main__":
    main()

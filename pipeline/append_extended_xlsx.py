"""Append the extended-range tables as new sheets to the existing workbook.

export_xlsx.py cannot be re-run (it reads /home/user/workspace/socal/data/out, which no longer
exists), so the workbook is patched in place instead of regenerated. Existing sheets are never
touched; the extended sheets are dropped and rewritten if already present, which makes this
idempotent.

Run after build_extended.py, before stage_downloads.py.
"""
import os

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV_DIR = os.path.join(ROOT, "dataset", "csv")
XLSX = os.path.join(ROOT, "dataset", "socal_fishing_dataset.xlsx")

# (csv filename, sheet name). Sheet names are capped at 31 chars by the format.
SHEETS = [
    ("forecast_confidence.csv", "confidence_tiers"),
    ("extended_outlook.csv", "extended_outlook"),
    ("extended_scores.csv", "extended_scores"),
    ("extended_field_provenance.csv", "extended_provenance"),
    ("cpc_outlook_current.csv", "cpc_outlook_current"),
    ("enso_analog_years.csv", "enso_analog_years"),
    ("enso_analog_zone_anomaly.csv", "enso_analog_zone_anom"),
]


def main():
    if not os.path.exists(XLSX):
        raise SystemExit(f"missing {XLSX}")
    wb = load_workbook(XLSX)
    kept = list(wb.sheetnames)

    for _, sheet in SHEETS:
        if sheet in wb.sheetnames:
            del wb[sheet]

    for fname, sheet in SHEETS:
        p = os.path.join(CSV_DIR, fname)
        if not os.path.exists(p):
            raise SystemExit(f"missing {p} -- run pipeline/build_extended.py first")
        df = pd.read_csv(p)
        ws = wb.create_sheet(sheet)
        ws.append(list(df.columns))
        for row in df.itertuples(index=False, name=None):
            ws.append(["" if pd.isna(v) else v for v in row])
        for c in ws[1]:
            c.font = Font(bold=True)
            c.alignment = Alignment(vertical="top")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, col in enumerate(df.columns, start=1):
            width = max(len(str(col)) + 2, min(38, int(df[col].astype(str).str.len().max() or 8) + 2))
            ws.column_dimensions[get_column_letter(i)].width = width
        print(f"  {sheet:24s} <- {fname:34s} {len(df):5d} rows x {len(df.columns)} cols")

    # Point the README sheet at the new sheets, and state the confidence tiers there too, so a
    # reader who opens the workbook without the dashboard cannot mistake a 25-day-out score for a
    # forecast. Rewritten between markers so re-running does not stack duplicate notes.
    if "README" in wb.sheetnames:
        rm = wb["README"]
        MARK = "--- 30-day extended outlook ---"
        rows = [rm.cell(r, 1).value for r in range(1, rm.max_row + 1)]
        cut = rows.index(MARK) + 1 if MARK in rows else len(rows) + 1
        for r in range(rm.max_row, cut - 1, -1):
            rm.delete_rows(r)
        # delete_rows leaves max_row inflated, so derive the real end from the last non-empty cell.
        # Without this the block drifts one blank row further down on every re-run.
        last = 0
        for r in range(1, rm.max_row + 1):
            v = rm.cell(r, 1).value
            if v not in (None, ""):
                last = r
        note = [
            MARK,
            "extended_outlook and extended_scores cover days 7-30 and are NOT the same kind of thing as",
            "conditions_daily and scores_daily. Read confidence_tiers first, then extended_provenance,",
            "which tags every extended field as forecast, blend, enso_analog, climatology, astronomical",
            "or metadata. Days 1-7 are high confidence. Days 8-14 are moderate. Days 15-30 are trend only:",
            "no daily wind or swell figure is published there, because no public extended marine forecast",
            "has useful skill at that lead. Past day 14 read the trend column, the tide and moon columns,",
            "and the score band -- not the daily score on its own.",
        ]
        first = last + 2  # one blank spacer row between the original notes and this block
        for i, line in enumerate(note):
            rm.cell(first + i, 1, line)
        rm.cell(first, 1).font = Font(bold=True)

    wb.save(XLSX)
    surviving = [s for s in kept if s in wb.sheetnames]
    assert len(surviving) == len(kept), f"lost pre-existing sheets: {set(kept) - set(wb.sheetnames)}"
    mb = os.path.getsize(XLSX) / 1e6
    print(f"saved {os.path.relpath(XLSX, ROOT)}  {mb:.2f} MB  "
          f"({len(kept)} original sheets preserved, {len(SHEETS)} extended sheets)")


if __name__ == "__main__":
    main()

"""Export the cleaned dataset as a formatted XLSX workbook plus a CSV bundle."""
import json, os, glob, shutil
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule

BASE = os.environ.get("SOCAL_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.environ.get("SOCAL_OUT", os.path.join(BASE, "dataset", "csv"))
DIST = os.environ.get("SOCAL_DIST", os.path.join(BASE, "dataset"))
os.makedirs(DIST, exist_ok=True)
XLSX = os.environ.get("SOCAL_XLSX", os.path.join(DIST, "socal_fishing_dataset.xlsx"))

SHEETS = [
    ("README", None),
    ("scores_daily", "Zone-species-day opportunity scores with driver decomposition"),
    ("forecast_7day", "Forward window only, ranked by score"),
    ("conditions_daily", "Zone-day environmental drivers, norms and anomalies"),
    ("climatological_curve", "Typical score for every day of the year"),
    ("dim_species", "Species model parameters"),
    ("dim_zones", "Zone definitions"),
    ("sst_climatology", "Satellite SST day-of-year climatology"),
    ("shore_sst_climatology", "Shore thermistor SST climatology"),
    ("buoy_climatology", "Buoy wave/wind/SST climatology"),
    ("shore_sst_daily", "Daily shore water temperature"),
    ("buoy_daily", "Daily buoy observations"),
    ("mur_history", "Daily satellite SST per zone"),
    ("tide_daily", "Daily tide features"),
    ("enso_oni", "Monthly ONI with regime labels"),
    ("enso_current", "ENSO state at build time"),
    ("enso_zone_composite", "SST anomaly composites by regime"),
    ("enso_sensitivity", "Zone SST anomaly sensitivity to ONI"),
    ("catch_reports", "Dock-total trip records"),
    ("catch_daily_cpue", "Daily CPUE by species"),
    ("model_validation", "Preliminary skill check"),
    ("source_registry", "Provenance for every feed"),
    ("data_dictionary", "Field-level definitions"),
    ("known_gaps", "Documented gaps and caveats"),
]

HDR_FILL = PatternFill("solid", fgColor="0F2534")
HDR_FONT = Font(color="E9F3F8", bold=True, size=10.5, name="Calibri")
TITLE_FONT = Font(bold=True, size=13, color="0F2534")
THIN = Side(style="thin", color="D3E0E8")


def load(name):
    p = f"{OUT}/{name}.csv"
    return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()


def main():
    docs = json.load(open(f"{OUT}/docs.json"))
    meta = json.load(open(f"{OUT}/run_meta.json"))
    scores = load("scores_daily")
    frames = {
        "source_registry": pd.DataFrame(docs["registry"])[
            ["name", "provider", "url", "endpoint", "used_for", "coverage", "cadence", "access", "units", "license", "limitations"]],
        "data_dictionary": pd.DataFrame(docs["dictionary"]),
        "known_gaps": pd.DataFrame({"gap": docs["gaps"]}),
        "forecast_7day": (scores[scores.is_forecast == True].sort_values("score", ascending=False)
                          if not scores.empty else pd.DataFrame()),
    }
    readme = pd.DataFrame({
        "SoCal Fishing Intelligence — cleaned dataset": [
            f"Build timestamp (UTC): {meta['run_utc']}",
            f"Zones: {meta['zones']} · Species: {meta['species']} · Score rows: {meta['score_rows']:,}",
            "",
            "Every sheet is machine-readable: one header row, no merged cells, US customary units.",
            "scores_daily is the primary output; conditions_daily holds the drivers behind it.",
            "seasonal_norm_score is the same model run on climatology, so score − seasonal_norm_score is",
            "the departure from what is typical for that date and place.",
            "Read data_dictionary for field definitions and source_registry for provenance.",
            "known_gaps lists what is missing or weakly supported — read it before trusting a number.",
            "",
            "Scoring: core = exp(Σ wᵢ·ln(termᵢ)/Σ wᵢ) × anomaly_term;  score = core × (0.35 + 0.65 × fishability) × 100",
            "",
            "Validation is a preliminary sanity check against 13 days of public dock totals, not a skill score.",
            "Regulations change: verify CDFW rules, and carry a Mexican FMM permit and licence for Mexican-water zones.",
        ]})

    with pd.ExcelWriter(XLSX, engine="openpyxl") as xw:
        readme.to_excel(xw, sheet_name="README", index=False)
        for name, _ in SHEETS:
            if name == "README":
                continue
            d = frames.get(name, load(name))
            if d.empty:
                continue
            if "date" in d.columns:
                d = d.copy(); d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            d.to_excel(xw, sheet_name=name[:31], index=False)

        wb = xw.book
        for ws in wb.worksheets:
            ws.freeze_panes = "A2"
            for c in ws[1]:
                c.fill = HDR_FILL; c.font = HDR_FONT
                c.alignment = Alignment(vertical="center", wrap_text=True)
            ws.row_dimensions[1].height = 30
            for col in ws.iter_cols(min_row=1, max_row=1):
                letter = get_column_letter(col[0].column)
                vals = [str(ws.cell(row=r, column=col[0].column).value or "")
                        for r in range(1, min(ws.max_row, 200) + 1)]
                w = max(10, min(52, max(len(v) for v in vals) + 2))
                ws.column_dimensions[letter].width = w
            if ws.max_row > 1:
                ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
        # colour scale on the score columns
        for sheet, col in [("scores_daily", "score"), ("forecast_7day", "score"), ("climatological_curve", "climatological_score")]:
            if sheet not in wb.sheetnames:
                continue
            ws = wb[sheet]
            hdr = [c.value for c in ws[1]]
            if col in hdr:
                letter = get_column_letter(hdr.index(col) + 1)
                ws.conditional_formatting.add(
                    f"{letter}2:{letter}{ws.max_row}",
                    ColorScaleRule(start_type="num", start_value=20, start_color="2A5B6B",
                                   mid_type="num", mid_value=60, mid_color="37A68E",
                                   end_type="num", end_value=95, end_color="F0913C"))
        rm = wb["README"]
        rm.column_dimensions["A"].width = 104
        rm["A1"].font = TITLE_FONT
        rm["A1"].fill = PatternFill("solid", fgColor="F2F6F9")

    print(f"{XLSX} ({os.path.getsize(XLSX)/1024:.0f} KB)")


if __name__ == "__main__":
    main()

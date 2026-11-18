"""NEW (Phase 2): find the CO2, SO2 and NOx air-emission factors in EPA's TRACI 2.2 spreadsheet.

Usage (from the project folder):
    python scripts/extract_traci.py path/to/traci_2_2.xlsx
    python scripts/extract_traci.py path/to/traci_2_2.xlsx -o epadata/data/traci_factors.csv

It reads the "Substances" sheet (one row per substance, one column per impact category, with the unit
in the column heading, e.g. "Acidification Air (kg SO2 eq / kg substance)") and writes a CSV that the
TRACI factors page can import. Substances are matched ONLY by exact CAS number or exact substance
name, never by approximate matching:
  * CARBON DIOXIDE  (CAS 124-38-9)  -> CAMPD CO2
  * SULFUR DIOXIDE  (CAS 7446-09-5) -> CAMPD SO2
  * NITROGEN OXIDES (the sheet's own "NITROGEN OXIDES" row) -> CAMPD NOx
The separate NITROGEN DIOXIDE and NITRIC OXIDE rows are also written, but marked verified = NO, as
alternatives you could choose instead. The website imports only rows with verified = yes, and allows
only one factor per pollutant and category.
"""
import argparse
import csv
import re
import sys

import openpyxl

CAS_TO_POLLUTANT = {"124389": "CO2", "7446095": "SO2"}   # CAS numbers without dashes
NOX_CHOSEN_NAME = "nitrogen oxides"                        # TRACI lists this as its own substance
NOX_ALTERNATIVES = {"nitrogen dioxide", "nitric oxide"}    # written, but verified = NO
FIELDS = ["traci_version", "campd_pollutant", "substance", "cas", "medium", "impact_category",
          "factor", "factor_unit", "verified", "source_note"]


def text(value):
    return "" if value is None else str(value).strip()


def plain_cas(value):
    """124389, '124389', 124389.0 and '124-38-9' all become '124389'."""
    t = text(value).replace("-", "")
    return t[:-2] if t.endswith(".0") else t


def dashed_cas(digits):
    """'124389' -> '124-38-9' (the last digit is the check digit, the two before it the middle part)."""
    return f"{digits[:-3]}-{digits[-3:-1]}-{digits[-1]}" if digits.isdigit() and len(digits) > 4 else digits


def find_header(rows):
    """The first row (within the top 25) that has a CAS column and a substance-name column."""
    for index, row in enumerate(rows[:25]):
        cells = [text(c).lower() for c in row]
        cas = next((i for i, c in enumerate(cells) if c in ("cas", "cas #", "cas no", "cas number", "cas no.")), None)
        name = next((i for i, c in enumerate(cells) if c in ("substance", "substance name", "name", "chemical", "flow")), None)
        if cas is not None and name is not None:
            return index, cas, name
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx")
    parser.add_argument("-o", "--output", default="traci_factors_review.csv")
    parser.add_argument("--version", default="2.2")
    args = parser.parse_args()

    book = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    found = []
    for sheet in book.worksheets:
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        header = find_header(rows)
        if not header:
            continue
        h, cas_col, name_col = header
        categories = []  # (column index, category name, unit)
        for i, cell in enumerate(rows[h]):
            label = text(cell)
            if i in (cas_col, name_col) or not re.search(r"\bair\s*\(", label, re.I):  # "<Category> Air (<unit>)"
                continue
            unit_match = re.search(r"\(([^)]*)\)", label)
            unit = unit_match.group(1) if unit_match else ""
            unit = re.sub(r"\s*substance\s*$", "", unit).strip()      # "kg CO2 eq/kg substance" -> "kg CO2 eq/kg"
            if unit and "/" not in unit:
                unit += "/kg"
            unit = re.sub(r"\s*/\s*", " / ", unit) or "?"             # tidy spaces around the slash
            category = re.sub(r"\(.*?\)", "", label)
            category = re.sub(r"\bair\b", "", category, flags=re.I).strip(" -:")
            categories.append((i, category, unit))
        if not categories:
            continue
        for row in rows[h + 1:]:
            cas, name = plain_cas(row[cas_col]), text(row[name_col])
            if cas in CAS_TO_POLLUTANT:
                pollutant, verified = CAS_TO_POLLUTANT[cas], "yes"
            elif name.lower() == NOX_CHOSEN_NAME:
                pollutant, verified = "NOX", "yes"
            elif name.lower() in NOX_ALTERNATIVES:
                pollutant, verified = "NOX", "NO"
            else:
                continue
            for col, category, unit in categories:
                value = row[col] if col < len(row) else None
                if value in (None, "") or not isinstance(value, (int, float)):
                    continue
                found.append({
                    "traci_version": args.version, "campd_pollutant": pollutant, "substance": name,
                    "cas": dashed_cas(cas) if cas.isdigit() else "", "medium": "air", "impact_category": category, "factor": value,
                    "factor_unit": unit, "verified": verified,
                    "source_note": f"sheet '{sheet.title}'",
                })

    if not found:
        print("Could not find CO2 / SO2 / nitrogen-oxide rows in the layout this script expects.")
        print("What the workbook contains (send this to whoever is helping you adjust the script):")
        for sheet in book.worksheets:
            print(f"\n--- sheet '{sheet.title}' ---")
            for row in list(sheet.iter_rows(values_only=True))[:6]:
                print([text(c)[:25] for c in row[:10]])
        sys.exit(1)

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(found)
    print(f"Wrote {len(found)} candidate rows to {args.output}:\n")
    for r in found:
        print(f"  {r['campd_pollutant']:4} {r['substance'][:28]:28} {r['impact_category'][:34]:34} "
              f"{r['factor']!s:>12} {r['factor_unit']:22} verified={r['verified']}")
    print("\nNext: check the rows, then import the CSV on the TRACI factors page (verified = yes rows only).")


if __name__ == "__main__":
    main()
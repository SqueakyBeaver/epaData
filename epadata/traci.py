# NEW FILE (Phase 2): EPA TRACI impact assessment.
#
# TRACI gives, for each pollutant and impact category, a "characterization factor" (CF): how much
# ONE KILOGRAM of the pollutant contributes to that category. CAMPD gives how much of each
# pollutant a unit emitted. Multiplying the two gives a "potential impact".
#
#   1. Mass_kg      = Mass_short_ton x 907.18474            (CAMPD reports short tons, TRACI wants kg)
#   2. Impact_c     = sum over pollutants i of ( Mass_i_kg x CF_i,c )     (one sum per category c)
#   3. Intensity_c  = Impact_c / Gross load (MWh)                          (optional, per MWh)
#
# Results are POTENTIAL impacts, used to compare things. They are not measured harm.
import csv
import io
import math
import re

import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from epadata.db.models import TraciFactor

SHORT_TON_KG = 907.18474  # 1 short ton = 907.18474 kilograms

# CAMPD pollutant -> the column of the analysis table that holds its mass (short tons)
POLLUTANT_COLUMNS = {"CO2": "co2_mass", "SO2": "so2_mass", "NOX": "nox_mass"}

# The columns a factor file must have (see traci_factors_template.csv)
REQUIRED_COLUMNS = (
    "traci_version", "campd_pollutant", "substance", "cas", "medium",
    "impact_category", "factor", "factor_unit", "verified",
)


def short_tons_to_kg(mass_short_tons):
    """Step 1: works on a number, a numpy array or a pandas Series (missing stays missing)."""
    return mass_short_tons * SHORT_TON_KG


def category_slug(category):
    """'Global warming' -> 'global_warming' (used in column names)."""
    return re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")


# ---------------------------------------------------------------------------------------------
# Importing factors
# ---------------------------------------------------------------------------------------------
def import_factors(session, text):
    """Read a reviewed factor file (CSV text) into the traci_factor table.

    Strict on purpose: the assignment says a factor must be matched to the CAMPD pollutant
    explicitly, never by approximate name matching. So
      * only rows with verified = yes are imported (the rest are counted and skipped),
      * the medium must be "air",
      * campd_pollutant must be exactly CO2, SO2 or NOX,
      * there can be only ONE verified factor per pollutant and category (two would be ambiguous).
    Importing replaces every stored factor of the same TRACI version, so it can be repeated safely.

    Returns {"ok": bool, "imported": n, "skipped": n, "version": str|None, "errors": [str]}.
    """
    errors = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        return {"ok": False, "imported": 0, "skipped": 0, "version": None,
                "errors": [f"The file is missing these columns: {', '.join(missing)}."]}

    rows, skipped, seen = [], 0, {}
    for number, raw in enumerate(reader, start=2):  # line 1 is the header
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        if row["verified"].lower() != "yes":
            skipped += 1
            continue
        pollutant = row["campd_pollutant"].upper()
        if pollutant not in POLLUTANT_COLUMNS:
            errors.append(f"Line {number}: campd_pollutant must be CO2, SO2 or NOX (found '{row['campd_pollutant']}').")
            continue
        if row["medium"].lower() != "air":
            errors.append(f"Line {number}: only air emissions are supported (found medium '{row['medium']}').")
            continue
        try:
            factor = float(row["factor"])
            if not math.isfinite(factor):
                raise ValueError
        except ValueError:
            errors.append(f"Line {number}: the factor '{row['factor']}' is not a number.")
            continue
        for needed in ("traci_version", "substance", "impact_category", "factor_unit"):
            if not row[needed]:
                errors.append(f"Line {number}: '{needed}' is empty.")
        key = (row["traci_version"], pollutant, row["impact_category"].lower())
        if key in seen:
            errors.append(
                f"Line {number}: {pollutant} already has a verified factor for "
                f"'{row['impact_category']}' (line {seen[key]}). Keep only one."
            )
            continue
        seen[key] = number
        rows.append((pollutant, factor, row))

    versions = {r["traci_version"] for _, _, r in rows}
    if len(versions) > 1:
        errors.append(f"The file mixes TRACI versions ({', '.join(sorted(versions))}). Import one version at a time.")
    if not rows and not errors:
        errors.append("No rows have verified = yes, so nothing was imported.")
    if errors:
        return {"ok": False, "imported": 0, "skipped": skipped, "version": None, "errors": errors}

    version = versions.pop()
    session.execute(delete(TraciFactor).where(TraciFactor.traci_version == version))
    for pollutant, factor, row in rows:
        session.add(TraciFactor(
            traci_version=version, campd_pollutant=pollutant, substance=row["substance"],
            cas=row["cas"] or None, medium="air", impact_category=row["impact_category"],
            factor=factor, factor_unit=row["factor_unit"], source_note=row.get("source_note") or None,
        ))
    session.commit()
    return {"ok": True, "imported": len(rows), "skipped": skipped, "version": version, "errors": []}


def load_factors(session, version=None):
    """The stored factors of one TRACI version (default: the newest stored) as a DataFrame."""
    if version is None:
        versions = sorted(set(session.scalars(select(TraciFactor.traci_version))))
        if not versions:
            return pd.DataFrame(columns=["traci_version", "campd_pollutant", "substance", "cas",
                                         "impact_category", "factor", "factor_unit"])
        version = versions[-1]
    rows = session.scalars(select(TraciFactor).where(TraciFactor.traci_version == version)
                           .order_by(TraciFactor.impact_category, TraciFactor.campd_pollutant)).all()
    return pd.DataFrame([
        {"traci_version": r.traci_version, "campd_pollutant": r.campd_pollutant, "substance": r.substance,
         "cas": r.cas, "impact_category": r.impact_category, "factor": r.factor, "factor_unit": r.factor_unit}
        for r in rows
    ], columns=["traci_version", "campd_pollutant", "substance", "cas",
                "impact_category", "factor", "factor_unit"])


# ---------------------------------------------------------------------------------------------
# Calculating impacts (steps 1-3 above)
# ---------------------------------------------------------------------------------------------
def calculate_impacts(df, factors):
    """Add one impact column per category (and one intensity column per MWh) to a copy of df.

    `df` is an analysis table (columns co2_mass, so2_mass, nox_mass in short tons, gross_load in MWh).
    `factors` is the table from load_factors().

    Rules:
      * Only pollutants that have a factor for a category are summed for that category, and
        categories are never added together (their units and meanings differ).
      * If any pollutant that belongs in a category is missing for a row, that row's result stays
        missing. It is NOT treated as zero, which would quietly understate the impact.
      * Intensity is undefined (missing) when gross load is zero, negative or missing.

    Returns (new_df, info) where info lists, per category, the unit, TRACI version and the factors used.
    """
    out = df.copy()
    info = {}
    for category, group in factors.groupby("impact_category", sort=False):
        total = pd.Series(0.0, index=out.index)
        used = {}
        for _, f in group.iterrows():
            column = POLLUTANT_COLUMNS[f["campd_pollutant"]]
            kg = short_tons_to_kg(out[column].astype(float))          # step 1
            total = total + kg * float(f["factor"])                    # step 2 (NaN stays NaN)
            used[f["campd_pollutant"]] = {
                "substance": f["substance"], "cas": f["cas"], "factor": float(f["factor"]),
            }
        slug = category_slug(category)
        unit = group["factor_unit"].iloc[0].split("/")[0].strip()  # "kg CO2 eq / kg" -> "kg CO2 eq"
        out[f"impact_{slug}"] = total
        load = out["gross_load"].astype(float)
        out[f"impact_{slug}_per_mwh"] = total / load.where(load > 0)  # step 3
        info[category] = {
            "column": f"impact_{slug}", "intensity_column": f"impact_{slug}_per_mwh",
            "unit": unit, "traci_version": group["traci_version"].iloc[0], "factors": used,
        }
    return out, info
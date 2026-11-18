# NEW FILE (Phase 2): the analytical data layer (the "analytical dataset builder").
#
# What it does, in order:
#   1. Filters      read the filters from the URL (so an analysis can be shared and repeated).
#   2. load_records pull the matching unit-year records out of the database as one pandas table.
#   3. add_indicators  add the derived columns (intensities, heat rate, utilization, year-over-year).
#   4. summarize / export_metadata   counts, missing-value summary, and what an export must record.
#
# Rule for every calculation here: a value that cannot be calculated stays MISSING (NaN, shown as a
# blank). It is never replaced by zero.
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import func, or_, select

from epadata.db.models import AnnualRecord, Dataset, Facility, Unit

ANALYSIS_VERSION = "epaData analysis 0.1 (Phase 2 data layer)"

# ---------------------------------------------------------------------------------------------
# The variables of an analysis table: key -> (label, unit, kind, definition)
# ---------------------------------------------------------------------------------------------
RAW_VARIABLES = {
    "operating_time": ("Operating time", "hours", "Hours the unit operated in the year (CAMPD)."),
    "gross_load": ("Gross load", "MWh", "Electricity generated, before the plant's own use (CAMPD)."),
    "heat_input": ("Heat input", "MMBtu", "Fuel energy burned (CAMPD)."),
    "co2_mass": ("CO2 mass", "short tons", "Carbon dioxide emitted (CAMPD)."),
    "so2_mass": ("SO2 mass", "short tons", "Sulfur dioxide emitted (CAMPD)."),
    "nox_mass": ("NOx mass", "short tons", "Nitrogen oxides emitted (CAMPD)."),
}

DERIVED_VARIABLES = {
    "co2_intensity": ("CO2 intensity", "short tons / MWh", "CO2 mass / gross load"),
    "so2_intensity": ("SO2 intensity", "short tons / MWh", "SO2 mass / gross load"),
    "nox_intensity": ("NOx intensity", "short tons / MWh", "NOx mass / gross load"),
    "heat_rate": ("Heat rate", "MMBtu / MWh", "heat input / gross load (lower = more efficient)"),
    "operating_utilization": (
        "Operating utilization", "fraction (0 to 1)",
        "operating time / hours in that year (8,760, or 8,784 in a leap year)"),
}
# Year-over-year change is added for these variables: <name>_yoy_abs and <name>_yoy_pct
YOY_BASE = ("co2_mass", "so2_mass", "nox_mass", "gross_load", "heat_input")
for _name in YOY_BASE:
    _label, _unit, _ = RAW_VARIABLES[_name]
    DERIVED_VARIABLES[f"{_name}_yoy_abs"] = (
        f"{_label}, change from previous year", _unit,
        "this year - previous year (missing if either year is missing, or the years are not consecutive)")
    DERIVED_VARIABLES[f"{_name}_yoy_pct"] = (
        f"{_label}, % change from previous year", "%",
        "(this year - previous year) / previous year x 100 (missing if the previous year is zero or missing)")

NUMERIC_VARIABLES = list(RAW_VARIABLES) + list(DERIVED_VARIABLES)
ID_COLUMNS = [
    "dataset_id", "facility_id", "facility_name", "state", "county", "unit_id", "unit_type",
    "primary_fuel", "year", "so2_controls", "nox_controls", "pm_controls", "program_code",
]
EXPORT_COLUMNS = ID_COLUMNS + NUMERIC_VARIABLES


# ---------------------------------------------------------------------------------------------
# 1. Filters (read from URL parameters)
# ---------------------------------------------------------------------------------------------
def _split(text):
    return [part.strip() for part in (text or "").split(",") if part.strip()]


@dataclass
class Filters:
    year_from: int | None = None
    year_to: int | None = None
    states: list[str] = field(default_factory=list)          # exact, e.g. KY,OH
    facility_ids: list[int] = field(default_factory=list)    # exact
    unit_ids: list[str] = field(default_factory=list)        # exact, e.g. 1,CT1
    fuels: list[str] = field(default_factory=list)           # "contains", e.g. Coal
    unit_types: list[str] = field(default_factory=list)      # "contains"
    controls: list[str] = field(default_factory=list)        # "contains", looked for in SO2/NOx/PM controls
    programs: list[str] = field(default_factory=list)        # "contains", e.g. ARP
    min_op_hours: float | None = None
    min_gross_load: float | None = None
    dataset_id: int | None = None                            # None = every dataset (newest copy wins)
    errors: list[str] = field(default_factory=list)          # problems found while reading the URL

    @classmethod
    def from_args(cls, args):
        f = cls()

        def number(name, kind, low=None, high=None):
            text = (args.get(name) or "").strip()
            if not text:
                return None
            try:
                value = kind(text)
            except ValueError:
                f.errors.append(f"'{text}' is not a valid number for {name.replace('_', ' ')}; it was ignored.")
                return None
            if (low is not None and value < low) or (high is not None and value > high):
                f.errors.append(f"{name.replace('_', ' ')} {text} is out of range; it was ignored.")
                return None
            return value

        f.year_from = number("year_from", int, 1990, 2100)
        f.year_to = number("year_to", int, 1990, 2100)
        if f.year_from and f.year_to and f.year_from > f.year_to:
            f.errors.append("'Year from' was after 'year to', so the two were swapped.")
            f.year_from, f.year_to = f.year_to, f.year_from
        f.states = [s.upper() for s in _split(args.get("state"))]
        for part in _split(args.get("facility_id")):
            if part.isdigit():
                f.facility_ids.append(int(part))
            else:
                f.errors.append(f"Facility ID '{part}' is not a number; it was ignored.")
        f.unit_ids = _split(args.get("unit_id"))
        f.fuels = _split(args.get("fuel"))
        f.unit_types = _split(args.get("unit_type"))
        f.controls = _split(args.get("control"))
        f.programs = _split(args.get("program"))
        f.min_op_hours = number("min_op_hours", float, 0, 8784)
        f.min_gross_load = number("min_gross_load", float, 0)
        f.dataset_id = number("dataset_id", int, 1)
        return f

    def to_query(self):
        """The filters as URL parameters (only the ones that are set). Same filters -> same result."""
        q = {}
        if self.year_from is not None:
            q["year_from"] = self.year_from
        if self.year_to is not None:
            q["year_to"] = self.year_to
        for key, values in (("state", self.states), ("facility_id", self.facility_ids),
                            ("unit_id", self.unit_ids), ("fuel", self.fuels),
                            ("unit_type", self.unit_types), ("control", self.controls),
                            ("program", self.programs)):
            if values:
                q[key] = ",".join(str(v) for v in values)
        if self.min_op_hours is not None:
            q["min_op_hours"] = self.min_op_hours
        if self.min_gross_load is not None:
            q["min_gross_load"] = self.min_gross_load
        if self.dataset_id is not None:
            q["dataset_id"] = self.dataset_id
        return q


# ---------------------------------------------------------------------------------------------
# 2. Loading records
# ---------------------------------------------------------------------------------------------
def _contains_any(series, terms):
    """True where the text contains ANY of the terms (not case sensitive). Missing text -> False."""
    text = series.fillna("").astype(str).str.lower()
    mask = pd.Series(False, index=series.index)
    for term in terms:
        mask |= text.str.contains(term.lower(), regex=False)
    return mask


def load_records(session, f):
    """One row per unit per reporting year that matches the filters, as a pandas table.

    * The same unit-year can exist in several datasets (a re-download, an upload). Counting both would
      double the totals, so when no single dataset is chosen the NEWEST dataset's copy is kept.
    * Year-over-year change needs the year BEFORE the first selected year, so that year is read too
      and removed again at the end.
    """
    stmt = (
        select(
            AnnualRecord.dataset_id, AnnualRecord.facility_id, Facility.name.label("facility_name"),
            Facility.state, Facility.county, Unit.internal_id.label("unit_key"),
            Unit.epa_unit_id.label("unit_id"), Unit.type.label("unit_type"), Unit.primary_fuel,
            AnnualRecord.reporting_year.label("year"),
            AnnualRecord.operating_time, AnnualRecord.gross_load, AnnualRecord.heat_input,
            AnnualRecord.co2_mass, AnnualRecord.so2_mass, AnnualRecord.nox_mass,
            AnnualRecord.so2_controls, AnnualRecord.nox_controls, AnnualRecord.pm_controls,
            AnnualRecord.program_code,
        )
        .join(Unit, AnnualRecord.unit_key == Unit.internal_id)
        .join(Facility, AnnualRecord.facility_id == Facility.id)
    )
    # Things that are the same in every copy of a record can be filtered in the database:
    if f.dataset_id is not None:
        stmt = stmt.where(AnnualRecord.dataset_id == f.dataset_id)
    if f.year_from is not None:
        stmt = stmt.where(AnnualRecord.reporting_year >= f.year_from - 1)
    if f.year_to is not None:
        stmt = stmt.where(AnnualRecord.reporting_year <= f.year_to)
    if f.states:
        stmt = stmt.where(Facility.state.in_(f.states))
    if f.facility_ids:
        stmt = stmt.where(Facility.id.in_(f.facility_ids))
    if f.unit_ids:
        stmt = stmt.where(func.lower(Unit.epa_unit_id).in_([u.lower() for u in f.unit_ids]))
    if f.fuels:
        stmt = stmt.where(_or_like(Unit.primary_fuel, f.fuels))
    if f.unit_types:
        stmt = stmt.where(_or_like(Unit.type, f.unit_types))

    rows = [dict(r) for r in session.execute(stmt).mappings().all()]
    if not rows:
        return _empty_table()
    df = pd.DataFrame(rows)
    for column in RAW_VARIABLES:  # blank stays blank (NaN); it is never turned into zero
        df[column] = pd.to_numeric(df[column], errors="coerce").astype(float)
    if f.dataset_id is None:
        df = df.sort_values("dataset_id").drop_duplicates(["unit_key", "year"], keep="last")
    return df.reset_index(drop=True)


def _or_like(column, terms):
    """SQL: the column contains ANY of the terms (not case sensitive)."""
    return or_(*[func.lower(column).like(f"%{t.lower()}%") for t in terms])


def _empty_table():
    return pd.DataFrame(columns=["unit_key"] + [c for c in EXPORT_COLUMNS if c not in DERIVED_VARIABLES]
                        ).astype({c: float for c in RAW_VARIABLES})


# ---------------------------------------------------------------------------------------------
# 3. Derived indicators
# ---------------------------------------------------------------------------------------------
def safe_divide(numerator, denominator):
    """numerator / denominator, but MISSING (NaN) wherever the answer is undefined:
    a missing number, or a denominator that is zero or negative. Never zero, never infinity."""
    return numerator.astype(float) / denominator.astype(float).where(denominator > 0)


def hours_in_year(years):
    """8,784 hours in a leap year, otherwise 8,760."""
    y = pd.Series(years).astype(int)
    leap = ((y % 4 == 0) & (y % 100 != 0)) | (y % 400 == 0)
    return pd.Series(np.where(leap, 8784, 8760), index=y.index)


def add_indicators(df):
    """Return a copy of df with the derived columns added (see DERIVED_VARIABLES)."""
    out = df.copy()
    if out.empty:
        for name in DERIVED_VARIABLES:
            out[name] = pd.Series(dtype=float)
        return out

    out["co2_intensity"] = safe_divide(out["co2_mass"], out["gross_load"])
    out["so2_intensity"] = safe_divide(out["so2_mass"], out["gross_load"])
    out["nox_intensity"] = safe_divide(out["nox_mass"], out["gross_load"])
    out["heat_rate"] = safe_divide(out["heat_input"], out["gross_load"])
    out["operating_utilization"] = out["operating_time"] / hours_in_year(out["year"]).set_axis(out.index)

    # Year-over-year: line each record up with the SAME unit's record from exactly one year earlier.
    previous = out[["unit_key", "year", *YOY_BASE]].copy()
    previous["year"] = previous["year"] + 1
    joined = out[["unit_key", "year"]].merge(previous, on=["unit_key", "year"], how="left")
    for name in YOY_BASE:
        change = out[name].to_numpy() - joined[name].to_numpy()
        out[f"{name}_yoy_abs"] = change
        out[f"{name}_yoy_pct"] = safe_divide(pd.Series(change, index=out.index),
                                             pd.Series(joined[name].to_numpy(), index=out.index)) * 100
    return out


def apply_record_filters(df, f):
    """Filters that depend on the record itself (not just the unit), applied AFTER choosing which copy
    of a record to keep, and then drop the extra year that was only loaded for year-over-year."""
    keep = pd.Series(True, index=df.index)
    if f.year_from is not None:
        keep &= df["year"] >= f.year_from
    if f.year_to is not None:
        keep &= df["year"] <= f.year_to
    if f.controls:
        combined = (df["so2_controls"].fillna("") + " | " + df["nox_controls"].fillna("")
                    + " | " + df["pm_controls"].fillna(""))
        keep &= _contains_any(combined, f.controls)
    if f.programs:
        keep &= _contains_any(df["program_code"], f.programs)
    if f.min_op_hours is not None:
        keep &= df["operating_time"] >= f.min_op_hours  # missing hours -> comparison False -> dropped
    if f.min_gross_load is not None:
        keep &= df["gross_load"] >= f.min_gross_load
    return df[keep].reset_index(drop=True)


def build_analysis_table(session, f):
    """The whole data layer in one call: filters -> records -> indicators -> record filters."""
    return apply_record_filters(add_indicators(load_records(session, f)), f)


# ---------------------------------------------------------------------------------------------
# 4. Summaries and export information
# ---------------------------------------------------------------------------------------------
def summarize(df):
    """Counts, and how many values are missing in each numeric variable."""
    counts = {
        "records": int(len(df)),
        "facilities": int(df["facility_id"].nunique()) if len(df) else 0,
        "units": int(df["unit_key"].nunique()) if len(df) else 0,
        "years": sorted(int(y) for y in df["year"].unique()) if len(df) else [],
    }
    labels = {**{k: (v[0], v[1]) for k, v in RAW_VARIABLES.items()},
              **{k: (v[0], v[1]) for k, v in DERIVED_VARIABLES.items()}}
    missing = []
    for name in NUMERIC_VARIABLES:
        n_missing = int(df[name].isna().sum()) if len(df) else 0
        missing.append({
            "variable": name, "label": labels[name][0], "unit": labels[name][1],
            "missing": n_missing, "present": counts["records"] - n_missing,
            "percent": round(100 * n_missing / counts["records"], 1) if counts["records"] else 0.0,
            "kind": "raw" if name in RAW_VARIABLES else "derived",
        })
    return {"counts": counts, "missing": missing}


def describe_filters(f):
    """The filters in plain words, one entry per filter that is set."""
    d = {}
    if f.year_from is not None or f.year_to is not None:
        d["Reporting years"] = f"{f.year_from or 'earliest'} to {f.year_to or 'latest'}"
    for label, values in (("States", f.states), ("Facility IDs", f.facility_ids),
                          ("Unit IDs", f.unit_ids), ("Primary fuel contains", f.fuels),
                          ("Unit type contains", f.unit_types),
                          ("Control technology contains", f.controls),
                          ("Emissions program contains", f.programs)):
        if values:
            d[label] = ", ".join(str(v) for v in values)
    if f.min_op_hours is not None:
        d["Minimum operating time (hours)"] = f.min_op_hours
    if f.min_gross_load is not None:
        d["Minimum gross load (MWh)"] = f.min_gross_load
    d["Dataset"] = f"dataset {f.dataset_id} only" if f.dataset_id else "all datasets (newest copy of each unit-year)"
    return d


def export_metadata(session, f, df, url=None):
    """What an exported file must record so the result can be reproduced."""
    ids = sorted({int(i) for i in df["dataset_id"].unique()}) if len(df) else []
    names = {d.id: d.name for d in session.scalars(select(Dataset).where(Dataset.id.in_(ids)))} if ids else {}
    meta = {
        "Analysis date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Software / pipeline version": ANALYSIS_VERSION,
        "Dataset identifiers": "; ".join(f"{i}: {names.get(i, '?')}" for i in ids) or "none",
        "Records / facilities / units": (
            f"{len(df)} / {df['facility_id'].nunique() if len(df) else 0} / "
            f"{df['unit_key'].nunique() if len(df) else 0}"),
    }
    for key, value in describe_filters(f).items():
        meta[f"Filter: {key}"] = value
    if url:
        meta["Link that repeats this analysis"] = url
    meta["Variables"] = ", ".join(c for c in EXPORT_COLUMNS)
    meta["Missing values"] = "Left blank. A value that cannot be calculated is never replaced with zero."
    for key, (label, unit, definition) in DERIVED_VARIABLES.items():
        meta[f"Definition: {key}"] = f"{label} [{unit}] = {definition}"
    return meta
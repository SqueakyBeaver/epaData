# NEW FILE (Phase 2): descriptive statistics, grouped statistics, rankings, comparisons and
# grouped TRACI impacts. Every function takes the table built by dataset_builder.build_analysis_table
# (one row per unit per year) and returns a pandas table that the pages show and the downloads save.
#
# Rule that applies everywhere: missing values are NOT zero. They are left out of means, medians and
# totals, and every result says how many were missing, so a number is never quietly based on less data.
import math

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from epadata import traci
from epadata.dataset_builder import DERIVED_VARIABLES, NUMERIC_VARIABLES, RAW_VARIABLES

PERCENTILES = (5, 10, 90, 95)  # the "selected percentiles" (the quartiles are always shown)

# Variables where adding values up makes sense (totals). Intensities, heat rate and so on are
# ratios, so a "total" of them would be meaningless.
SUMMABLE = ("co2_mass", "so2_mass", "nox_mass", "gross_load", "heat_input")

GROUP_DIMENSIONS = {
    "state": "State",
    "year": "Year",
    "primary_fuel": "Primary fuel",
    "unit_type": "Unit type",
    "facility": "Facility",
    "unit": "Unit",
}
MAX_GROUP_ROWS = 200  # the page shows at most this many groups; the download has all of them

# the variables the comparison reports on
COMPARE_VARIABLES = (
    "co2_mass", "so2_mass", "nox_mass", "gross_load", "heat_input",
    "co2_intensity", "so2_intensity", "nox_intensity", "heat_rate",
)


def variable_info():
    """key -> (label, unit) for every numeric variable."""
    info = {k: (v[0], v[1]) for k, v in RAW_VARIABLES.items()}
    info.update({k: (v[0], v[1]) for k, v in DERIVED_VARIABLES.items()})
    return info


def group_labels(df, dimension):
    """One label per row for the chosen grouping, e.g. 'KY', 2020, 'Plant Name (1355)'."""
    if dimension == "state":
        return df["state"].fillna("(unknown)")
    if dimension == "year":
        return df["year"]
    if dimension in ("primary_fuel", "unit_type"):
        return df[dimension].fillna("(not reported)")
    if dimension == "facility":
        return df["facility_name"].astype(str) + " (" + df["facility_id"].astype(str) + ")"
    if dimension == "unit":
        return (df["facility_name"].astype(str) + " (" + df["facility_id"].astype(str)
                + ") unit " + df["unit_id"].astype(str))
    raise ValueError(f"unknown grouping '{dimension}'")


# ---------------------------------------------------------------------------------------------
# Descriptive statistics for one variable
# ---------------------------------------------------------------------------------------------
def describe(series):
    """Count, missing, min, max, mean, median, standard deviation, quartiles, IQR, percentiles.

    The standard deviation is the SAMPLE standard deviation (divides by n - 1). With fewer than
    two values it cannot be calculated and is left missing.
    """
    s = pd.to_numeric(series, errors="coerce").astype(float)
    present = s.dropna()
    nan = float("nan")
    out = {
        "Count (not missing)": int(len(present)),
        "Missing": int(s.isna().sum()),
        "Minimum": present.min() if len(present) else nan,
        "Maximum": present.max() if len(present) else nan,
        "Mean": present.mean() if len(present) else nan,
        "Median": present.median() if len(present) else nan,
        "Standard deviation": present.std(ddof=1) if len(present) > 1 else nan,
        "First quartile (Q1)": present.quantile(0.25) if len(present) else nan,
        "Third quartile (Q3)": present.quantile(0.75) if len(present) else nan,
    }
    out["Interquartile range (Q3 - Q1)"] = out["Third quartile (Q3)"] - out["First quartile (Q1)"]
    for p in PERCENTILES:
        out[f"{p}th percentile"] = present.quantile(p / 100) if len(present) else nan
    return out


# ---------------------------------------------------------------------------------------------
# Grouped statistics and rankings
# ---------------------------------------------------------------------------------------------
def grouped_stats(df, dimension, variable):
    """Statistics of one variable for each group (state, year, fuel, ...).

    Columns: records (all unit-years in the group), count (with a value), missing, mean, median,
    standard deviation, min, max and - only for variables where a total makes sense - total.
    """
    if df.empty:
        return pd.DataFrame()
    work = pd.DataFrame({"group": group_labels(df, dimension), "value": pd.to_numeric(df[variable], errors="coerce")})
    g = work.groupby("group", dropna=False)["value"]
    out = pd.DataFrame({
        "records": g.size(),
        "count": g.count(),
        "missing": g.size() - g.count(),
        "mean": g.mean(),
        "median": g.median(),
        "std": g.std(ddof=1),
        "min": g.min(),
        "max": g.max(),
    })
    if variable in SUMMABLE:
        out["total"] = g.sum(min_count=1)  # all-missing group -> total stays missing, not 0
    if dimension == "year":
        out = out.sort_index()
    else:
        out = out.sort_values("total" if "total" in out else "mean", ascending=False)
    out.index.name = GROUP_DIMENSIONS[dimension]
    return out


def rank(df, level, variable, aggregate, n, direction):
    """Top-N (direction 'top') or Bottom-N ('bottom') groups by an aggregate of a variable.

    aggregate is 'sum' (only for variables where totals make sense), 'mean' or 'median'. Groups
    whose values are ALL missing are left out of the ranking (they cannot be ranked).
    """
    if df.empty:
        return pd.DataFrame()
    if aggregate == "sum" and variable not in SUMMABLE:
        aggregate = "mean"
    work = pd.DataFrame({"group": group_labels(df, level), "value": pd.to_numeric(df[variable], errors="coerce")})
    g = work.groupby("group")["value"]
    out = pd.DataFrame({"value": g.agg(aggregate), "unit_years_with_value": g.count(), "unit_years": g.size()})
    out = out[out["unit_years_with_value"] > 0].sort_values("value", ascending=(direction == "bottom"))
    out = out.head(n).reset_index()
    out.insert(0, "rank", range(1, len(out) + 1))
    out = out.rename(columns={"group": GROUP_DIMENSIONS[level]})
    out.attrs["aggregate"] = aggregate
    return out


# ---------------------------------------------------------------------------------------------
# Comparisons between two groups
# ---------------------------------------------------------------------------------------------
def _terms(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def _contains_any(series, terms):
    text = series.fillna("").astype(str).str.lower()
    mask = pd.Series(False, index=series.index)
    for t in terms:
        mask |= text.str.contains(t.lower(), regex=False)
    return mask


def _year_set(text):
    """'2015' -> {2015}; '2010-2014' -> {2010..2014}; '2010, 2012' -> {2010, 2012}; bad text -> None."""
    years = set()
    for part in _terms(text):
        try:
            if "-" in part:
                low, high = (int(x) for x in part.split("-", 1))
                years |= set(range(min(low, high), max(low, high) + 1))
            else:
                years.add(int(part))
        except ValueError:
            return None
    return years or None


def split_groups(df, dimension, a, b):
    """Split the table into group A and group B. Returns (dfA, dfB, labelA, labelB, error)."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or (dimension != "control" and not b):
        return None, None, "", "", "Fill in both groups."
    if dimension == "state":
        sa, sb = {t.upper() for t in _terms(a)}, {t.upper() for t in _terms(b)}
        ma, mb = df["state"].isin(sa), df["state"].isin(sb)
        la, lb = a.upper(), b.upper()
    elif dimension in ("primary_fuel", "unit_type"):
        ma, mb = _contains_any(df[dimension], _terms(a)), _contains_any(df[dimension], _terms(b))
        la, lb = a, b
    elif dimension == "year":
        ya, yb = _year_set(a), _year_set(b)
        if ya is None or yb is None:
            return None, None, "", "", "Years must look like 2015, 2010-2014 or 2010, 2012."
        ma, mb = df["year"].isin(ya), df["year"].isin(yb)
        la, lb = a, b
    elif dimension == "control":
        combined = (df["so2_controls"].fillna("") + " | " + df["nox_controls"].fillna("")
                    + " | " + df["pm_controls"].fillna(""))
        ma = _contains_any(combined, _terms(a))
        mb = ~ma
        la, lb = f"with '{a}'", f"without '{a}'"
    else:
        return None, None, "", "", "Unknown comparison."
    overlap = int((ma & mb).sum())
    if overlap:
        return None, None, "", "", (
            f"{overlap} record(s) belong to both groups, so the comparison would count them twice. "
            "Make the two groups distinct.")
    if not ma.any() or not mb.any():
        side = "A" if not ma.any() else "B"
        return None, None, "", "", f"Group {side} has no records with these filters."
    return df[ma], df[mb], la, lb, None


def _welch(a, b):
    """Welch's two-sample t-test and 95% confidence interval for (mean B - mean A).
    Returns (p, ci_low, ci_high), or NaNs when a group has fewer than 2 values."""
    nan = float("nan")
    if len(a) < 2 or len(b) < 2:
        return nan, nan, nan
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return nan, nan, nan
    diff = b.mean() - a.mean()
    dof = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    p = 2 * scipy_stats.t.sf(abs(diff / se), dof)
    margin = scipy_stats.t.ppf(0.975, dof) * se
    return float(p), diff - margin, diff + margin


def _mann_whitney(a, b):
    """Rank-based test (does not assume a bell curve). Returns the p-value or NaN."""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    try:
        return float(scipy_stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)
    except ValueError:  # e.g. every value identical
        return float("nan")


def _verdict(p_welch, p_mw):
    """Plain-language reading of the two p-values (the usual cut-off is 0.05)."""
    ps = [p for p in (p_welch, p_mw) if not math.isnan(p)]
    if not ps:
        return "not enough data"
    if all(p < 0.05 for p in ps):
        return "yes"
    if any(p < 0.05 for p in ps):
        return "unclear (the two tests disagree)"
    return "no"


def compare_groups(dfa, dfb):
    """One row per variable: size, mean and median of each group, the differences (B minus A),
    a 95% confidence interval for the difference in means, and two p-values."""
    rows = []
    info = variable_info()
    for var in COMPARE_VARIABLES:
        a = pd.to_numeric(dfa[var], errors="coerce").dropna().astype(float)
        b = pd.to_numeric(dfb[var], errors="coerce").dropna().astype(float)
        mean_a = a.mean() if len(a) else float("nan")
        mean_b = b.mean() if len(b) else float("nan")
        median_a = a.median() if len(a) else float("nan")
        median_b = b.median() if len(b) else float("nan")
        diff = mean_b - mean_a
        p_welch, ci_low, ci_high = _welch(a, b)
        p_mw = _mann_whitney(a, b)
        rows.append({
            "Variable": info[var][0], "Unit": info[var][1],
            "A: values": len(a), "A: mean": mean_a, "A: median": median_a,
            "B: values": len(b), "B: mean": mean_b, "B: median": median_b,
            "Difference in means (B - A)": diff,
            "% difference (relative to A's mean)": diff / mean_a * 100 if mean_a and mean_a > 0 else float("nan"),
            "Difference in medians (B - A)": median_b - median_a,
            "95% CI of difference: low": ci_low, "95% CI of difference: high": ci_high,
            "p-value (Welch t-test)": p_welch, "p-value (Mann-Whitney)": p_mw,
            "Detectable difference?": _verdict(p_welch, p_mw),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# TRACI impacts, totalled by group
# ---------------------------------------------------------------------------------------------
def impact_by_group(df, factors, dimension):
    """Potential TRACI impact of each group, one row per group and impact category.

    * total_impact adds up the unit-years that HAVE a result (a unit-year with a missing pollutant
      amount has no result and is counted in unit_years_missing).
    * impact_per_mwh = total impact / total gross load, using only unit-years that have both a result and a
      positive gross load, so the top and bottom of the fraction cover exactly the same records.

    Returns (long_table, info). info is from traci.calculate_impacts (units, TRACI version, factors).
    """
    if df.empty or factors.empty:
        return pd.DataFrame(), {}
    calc, info = traci.calculate_impacts(df, factors)
    calc = calc.assign(group=group_labels(df, dimension).to_numpy())
    pieces = []
    for category, meta in info.items():
        col = meta["column"]
        has = calc[calc[col].notna()]
        with_load = has[has["gross_load"] > 0]
        groups = pd.Index(calc["group"].unique(), name=GROUP_DIMENSIONS[dimension])
        table = pd.DataFrame(index=groups)
        table["impact_category"] = category
        table["unit"] = meta["unit"]
        table["unit_years_included"] = has.groupby("group").size().reindex(groups, fill_value=0)
        table["unit_years_missing"] = calc[calc[col].isna()].groupby("group").size().reindex(groups, fill_value=0)
        table["total_impact"] = has.groupby("group")[col].sum().reindex(groups)  # no records -> missing
        load = with_load.groupby("group")["gross_load"].sum().reindex(groups)
        table["impact_per_mwh"] = with_load.groupby("group")[col].sum().reindex(groups) / load.where(load > 0)
        table["traci_version"] = meta["traci_version"]
        pieces.append(table.reset_index())
    long = pd.concat(pieces, ignore_index=True)
    return long, info
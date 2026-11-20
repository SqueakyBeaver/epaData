# NEW FILE (Phase 2): the analysis pages. Every page takes the same filters from the URL.
#   /analysis/            build an analytical dataset (filters in the URL), see counts + missing values
#   /analysis/download    the same dataset as CSV / Excel, with the metadata that makes it reproducible
#   /analysis/stats       descriptive statistics, grouped statistics, rankings
#   /analysis/compare     compare two groups (states, fuels, unit types, periods, with / without a control)
#   /analysis/impact      potential TRACI impacts, totalled by state, year, fuel, facility ...
#   /analysis/traci       the stored TRACI factors, and where a reviewed factor file is imported
import math
import numbers
from pathlib import Path

import pandas as pd
from flask import Blueprint, render_template, request, url_for
from sqlalchemy import select

from epadata import dataset_builder, summary_stats, traci
from epadata.db import Session
from epadata.db.models import Dataset, TraciFactor
from epadata.routes.utils import table_download

bp = Blueprint("analysis", __name__, url_prefix="/analysis")

# The TRACI 2.2 factors for CO2, SO2 and NOx, made from EPA's own spreadsheet by scripts/extract_traci.py
BUNDLED_TRACI_FILE = Path(__file__).resolve().parent.parent / "data" / "traci_factors.csv"

def _fmt(value):
    """Text for a number in a table. A missing value is an em dash, never 0."""
    if isinstance(value, bool) or value is None:
        return "\u2014" if value is None else str(value)
    if isinstance(value, numbers.Integral):
        return f"{int(value):,}"
    v = float(value)
    if math.isnan(v):
        return "\u2014"
    if v == 0:
        return "0"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:,.2f}"
    return f"{v:.3g}"


def _table(df, index=True):
    """(column names, rows of text) for a template. The index becomes the first column.
    A "Year" column is written plainly (2020, not 2,020)."""
    if index:
        df = df.reset_index()
    columns = [str(c) for c in df.columns]
    rows = []
    for row in df.itertuples(index=False):
        cells = []
        for name, v in zip(columns, row):
            if isinstance(v, str):
                cells.append(v)
            elif name == "Year" and isinstance(v, numbers.Integral):
                cells.append(str(int(v)))
            else:
                cells.append(_fmt(v))
        rows.append(cells)
    return columns, rows


def _choice(name, allowed, default):
    value = request.args.get(name, default)
    return value if value in allowed else default


def _common_context(f, dataset_choices):
    """What every analysis page needs: the filters, the dataset list and the link to repeat them."""
    # NEW: the lists for the filter drop-downs. A value that came in through a typed link but is
    # not in the database is kept in its list so it still shows as selected.
    with Session() as session:
        found = dict(dataset_builder.filter_options(session))
    known = {code for code, _ in found["states"]}
    found["states"] = found["states"] + [(c, c) for c in f.states if c not in known]
    for key, chosen in (("fuels", f.fuels), ("unit_types", f.unit_types),
                        ("controls", f.controls), ("programs", f.programs)):
        found[key] = found[key] + [v for v in chosen if v not in found[key]]
    return dict(f=f, form=request.args, dataset_choices=dataset_choices, options=found,
                filter_query=f.to_query(), filter_text=dataset_builder.describe_filters(f))


def _dataset_choices(session):
    return [(d.id, d.name) for d in session.scalars(select(Dataset).order_by(Dataset.id.desc())).all()]


def _download_url(**extra):
    """The same page address with download=... and optionally format=xlsx added."""
    args = request.args.to_dict()
    args.update(extra)
    return url_for(request.endpoint, **args)


PREVIEW_ROWS = 50
# the columns shown in the preview table (the download has all of them)
PREVIEW_COLUMNS = [
    ("year", "Year", 0), ("state", "State", None), ("facility_name", "Facility", None),
    ("unit_id", "Unit", None), ("primary_fuel", "Fuel", None),
    ("gross_load", "Gross load (MWh)", 0), ("co2_mass", "CO2 (short tons)", 0),
    ("co2_intensity", "CO2 intensity (short tons/MWh)", 3), ("so2_intensity", "SO2 intensity (short tons/MWh)", 5),
    ("nox_intensity", "NOx intensity (short tons/MWh)", 5), ("heat_rate", "Heat rate (MMBtu/MWh)", 2),
    ("co2_mass_yoy_pct", "CO2 change from previous year (%)", 1),
]


def _cell(value, decimals):
    """Text for a table cell. A missing value is shown as an em dash, never as 0."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if decimals is None:
        return str(value)
    return f"{float(value):,.{decimals}f}"


@bp.get("/")
def index():
    f = dataset_builder.Filters.from_args(request.args)
    result = None
    if request.args:  # the form was submitted (or a shared link was opened)
        with Session() as session:
            df = dataset_builder.build_analysis_table(session, f)
        result = dataset_builder.summarize(df)
        result["preview"] = [
            [_cell(row[col], dec) for col, _, dec in PREVIEW_COLUMNS]
            for _, row in df.head(PREVIEW_ROWS).iterrows()
        ]
        result["shown"] = min(len(df), PREVIEW_ROWS)
    with Session() as session:
        dataset_choices = _dataset_choices(session)
    query = f.to_query()
    return render_template(
        "analysis.html",
        **_common_context(f, dataset_choices), result=result,
        columns=[label for _, label, _ in PREVIEW_COLUMNS],
        share_url=url_for("analysis.index", _external=True, **query) if query else None,
        csv_url=url_for("analysis.download", **query),
        xlsx_url=url_for("analysis.download", format="xlsx", **query),
        raw_variables=dataset_builder.RAW_VARIABLES, derived_variables=dataset_builder.DERIVED_VARIABLES,
    )


@bp.get("/download")
def download():
    f = dataset_builder.Filters.from_args(request.args)
    with Session() as session:
        df = dataset_builder.build_analysis_table(session, f)
        meta = dataset_builder.export_metadata(
            session, f, df, url=url_for("analysis.index", _external=True, **f.to_query()))
    return table_download(df[dataset_builder.EXPORT_COLUMNS], "epadata-analysis", metadata=meta)


@bp.route("/traci", methods=["GET", "POST"])
def traci_page():
    message = None
    with Session() as session:
        if request.method == "POST" and request.form.get("bundled"):
            # the button "Import the factors included with the project"
            message = traci.import_factors(session, BUNDLED_TRACI_FILE.read_text(encoding="utf-8"))
        elif request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename:
                message = {"ok": False, "errors": ["Choose a .csv file first."]}
            else:
                try:
                    text = upload.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = None
                    message = {"ok": False, "errors": ["The file is not a UTF-8 text/CSV file."]}
                if text is not None:
                    message = traci.import_factors(session, text)
        factors = traci.load_factors(session)
    return render_template(
        "traci.html", message=message, factors=factors.to_dict("records"),
        required_columns=traci.REQUIRED_COLUMNS, short_ton_kg=traci.SHORT_TON_KG,
    )


# ---------------------------------------------------------------------------------------------
# Statistics: descriptive, grouped, rankings
# ---------------------------------------------------------------------------------------------
@bp.get("/stats")
def stats():
    f = dataset_builder.Filters.from_args(request.args)
    info = summary_stats.variable_info()
    variable = _choice("variable", info, "co2_mass")
    group_by = _choice("group_by", summary_stats.GROUP_DIMENSIONS, "state")
    rank_level = _choice("rank_level", summary_stats.GROUP_DIMENSIONS, "facility")
    rank_agg = _choice("rank_agg", ("sum", "mean", "median"), "sum")
    rank_dir = _choice("rank_dir", ("top", "bottom"), "top")
    rank_n = min(max(request.args.get("rank_n", 10, type=int), 1), 100)
    label, unit = info[variable]

    with Session() as session:
        df = dataset_builder.build_analysis_table(session, f)
        described = pd.Series(summary_stats.describe(df[variable])).to_frame("value") if len(df) else None
        grouped = summary_stats.grouped_stats(df, group_by, variable)
        ranked = summary_stats.rank(df, rank_level, variable, rank_agg, rank_n, rank_dir)
        if len(ranked):  # friendlier column names (the page and the download use the same ones)
            word = {"sum": "Total", "mean": "Average", "median": "Median"}[ranked.attrs["aggregate"]]
            ranked = ranked.rename(columns={
                "rank": "Rank", "value": f"{word} {label.lower()} ({unit})",
                "unit_years_with_value": "Unit-years with a value", "unit_years": "Unit-years"})

        download = request.args.get("download")
        if download in ("describe", "grouped", "rank"):
            meta = dataset_builder.export_metadata(session, f, df, url=url_for("analysis.stats", _external=True, **request.args.to_dict()))
            meta["Variable"] = f"{label} [{unit}]"
            meta["Standard deviation"] = "sample standard deviation (divides by n - 1)"
            meta["Quartiles / percentiles"] = "linear interpolation between the closest values"
            if download == "describe":
                out, name = described.reset_index().rename(columns={"index": "statistic"}), f"epadata-describe-{variable}"
            elif download == "grouped":
                meta["Grouped by"] = summary_stats.GROUP_DIMENSIONS[group_by]
                out, name = grouped.reset_index(), f"epadata-{variable}-by-{group_by}"
            else:
                meta["Ranking"] = f"{rank_dir} {rank_n} {summary_stats.GROUP_DIMENSIONS[rank_level]} by {rank_agg} of {label}"
                out, name = ranked, f"epadata-ranking-{variable}"
            return table_download(out, name, metadata=meta)
        dataset_choices = _dataset_choices(session)

    shown = grouped.head(summary_stats.MAX_GROUP_ROWS)
    return render_template(
        "analysis_stats.html", **_common_context(f, dataset_choices),
        active="stats", records=len(df),
        variables=[(k, v[0], v[1]) for k, v in info.items()], variable=variable, var_label=label, var_unit=unit,
        group_dims=summary_stats.GROUP_DIMENSIONS, group_by=group_by,
        described=[(k, _fmt(int(v)) if k in ("Count (not missing)", "Missing") else _fmt(v))
                   for k, v in described["value"].items()] if described is not None else None,
        grouped=_table(shown) if len(grouped) else None, grouped_total=len(grouped), grouped_shown=len(shown),
        rank_level=rank_level, rank_agg=rank_agg, rank_dir=rank_dir, rank_n=rank_n,
        ranked=_table(ranked, index=False) if len(ranked) else None,
        rank_agg_used=ranked.attrs.get("aggregate", rank_agg) if len(ranked) else rank_agg,
        dl=lambda what, fmt="csv": _download_url(download=what, **({"format": "xlsx"} if fmt == "xlsx" else {})),
    )


# ---------------------------------------------------------------------------------------------
# Comparison of two groups
# ---------------------------------------------------------------------------------------------
COMPARE_DIMENSIONS = {
    "state": "State(s)",
    "primary_fuel": "Primary fuel (contains)",
    "unit_type": "Unit type (contains)",
    "year": "Reporting period (years)",
    "control": "With and without a control technology",
}


@bp.get("/compare")
def compare():
    f = dataset_builder.Filters.from_args(request.args)
    dimension = _choice("dimension", COMPARE_DIMENSIONS, "state")
    a, b = request.args.get("a", ""), request.args.get("b", "")
    result = error = None
    labels = ("", "")
    with Session() as session:
        if request.args.get("dimension") is not None:  # the form was submitted
            df = dataset_builder.build_analysis_table(session, f)
            dfa, dfb, la, lb, error = summary_stats.split_groups(df, dimension, a, b)
            if not error:
                labels = (la, lb)
                result = summary_stats.compare_groups(dfa, dfb)
                counts = (len(dfa), len(dfb), dfa["unit_key"].nunique(), dfb["unit_key"].nunique())
                if request.args.get("download"):
                    meta = dataset_builder.export_metadata(session, f, df, url=url_for("analysis.compare", _external=True, **request.args.to_dict()))
                    meta.update({
                        "Comparison": f"{COMPARE_DIMENSIONS[dimension]}: A = {la}, B = {lb}",
                        "Differences": "B minus A. Percentage difference is relative to A's mean.",
                        "Welch t-test": "two-sample t-test that does not assume equal spread; 95% confidence interval for the difference in means",
                        "Mann-Whitney": "rank-based test that does not assume a bell-shaped distribution",
                        "Caution": "unit-years from the same unit are not independent, so p-values are optimistic",
                    })
                    return table_download(result, "epadata-comparison", metadata=meta)
        dataset_choices = _dataset_choices(session)
    display = None
    if result is not None:
        display = _table(result, index=False)
    return render_template(
        "analysis_compare.html", **_common_context(f, dataset_choices), active="compare",
        dimensions=COMPARE_DIMENSIONS, dimension=dimension, a=a, b=b, error=error,
        display=display, labels=labels, counts=counts if result is not None else None,
        submitted=request.args.get("dimension") is not None,
        dl=lambda fmt="csv": _download_url(download=1, **({"format": "xlsx"} if fmt == "xlsx" else {})),
    )


# ---------------------------------------------------------------------------------------------
# Potential TRACI impacts
# ---------------------------------------------------------------------------------------------
@bp.get("/impact")
def impact():
    f = dataset_builder.Filters.from_args(request.args)
    group_by = _choice("group_by", summary_stats.GROUP_DIMENSIONS, "state")
    with Session() as session:
        factors = traci.load_factors(session, request.args.get("version") or None)
        versions = sorted(set(session.scalars(select(TraciFactor.traci_version))))
        df = dataset_builder.build_analysis_table(session, f)
        long, info = summary_stats.impact_by_group(df, factors, group_by)
        if request.args.get("download") and len(long):
            meta = dataset_builder.export_metadata(session, f, df, url=url_for("analysis.impact", _external=True, **request.args.to_dict()))
            meta["TRACI version"] = ", ".join(sorted({m["traci_version"] for m in info.values()}))
            meta["Grouped by"] = summary_stats.GROUP_DIMENSIONS[group_by]
            meta["Method"] = ("mass_kg = short tons x 907.18474; impact = sum over pollutants of mass_kg x TRACI factor, "
                              "added up only within the same impact category; impact_per_mwh = total impact / total gross load "
                              "(unit-years with a result and a positive gross load)")
            for category, m in info.items():
                meta[f"Factors: {category} [{m['unit']}]"] = "; ".join(
                    f"{p} = {v['factor']} ({v['substance']})" for p, v in m["factors"].items())
            meta["Meaning"] = "Potential impacts for comparison. They do not measure actual harm, local exposure or illness."
            return table_download(long, "epadata-traci-impact", metadata=meta)
        dataset_choices = _dataset_choices(session)

    sections = []
    for category, m in info.items():
        part = long[long["impact_category"] == category].sort_values("total_impact", ascending=False)
        shown = part.head(summary_stats.MAX_GROUP_ROWS).drop(columns=["impact_category", "unit", "traci_version"])
        shown = shown.rename(columns={
            "unit_years_included": "unit-years included", "unit_years_missing": "unit-years missing",
            "total_impact": f"total impact ({m['unit']})", "impact_per_mwh": f"impact per MWh ({m['unit']}/MWh)"})
        sections.append({"category": category, "unit": m["unit"], "version": m["traci_version"],
                         "factors": m["factors"], "table": _table(shown, index=False), "total_groups": len(part),
                         "shown_groups": len(shown)})
    return render_template(
        "analysis_impact.html", **_common_context(f, dataset_choices), active="impact",
        group_dims=summary_stats.GROUP_DIMENSIONS, group_by=group_by, sections=sections,
        has_factors=len(factors) > 0, versions=versions, version=request.args.get("version", ""),
        records=len(df), short_ton_kg=traci.SHORT_TON_KG,
        dl=lambda fmt="csv": _download_url(download=1, **({"format": "xlsx"} if fmt == "xlsx" else {})),
    )
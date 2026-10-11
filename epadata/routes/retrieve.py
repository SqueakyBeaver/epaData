from datetime import datetime  # NEW: names each retrieval

import pandas as pd
from flask import Blueprint, Response, redirect, render_template, request, url_for
from sqlalchemy import select

from epadata import search as fulltext  # full-text search (Whoosh)
from epadata.campd import CAMPDClient
from epadata.db import Session  # NEW: retrieved data is now saved in the database
from epadata.db.models import AnnualRecord, Dataset, Facility, Unit  # NEW
from epadata.importer import import_annual_data, parse_years  # NEW

bp = Blueprint("retrieve", __name__, url_prefix="/retrieve")


# ---- NEW: full-text search box (searches ALL saved data, independent of the filters) ----


def wants_fragment():
    """NEW: True when htmx wants only a piece of the page (not a reload or the back button)."""
    h = request.headers
    return (
        h.get("HX-Request") == "true" and h.get("HX-History-Restore-Request") != "true"
    )


@bp.after_request
def vary_on_htmx(resp):
    # NEW: the same URL returns a fragment or a whole page, so caches must know
    resp.vary.add("HX-Request")
    return resp


@bp.get("/search/download")
def fulltext_download():
    """NEW: every row matching the full-text search (not just one page) as a CSV."""
    ft = fulltext.search(
        request.args.get("q", ""), per_page=None, relax=request.args.get("relax") == "1"
    )
    if ft["ranking"]:  # NEW: a "top 10 ..." sentence downloads the ranking, not a text search
        return redirect(url_for("explore.rank_download", **ft["ranking"]["args"]))
    columns = {
        "facility_id": "Facility ID",
        "facility_name": "Facility Name",
        "state": "State",
        "county": "County",
        "unit_id": "Unit ID",
        "unit_type": "Unit Type",
        "primary_fuel": "Primary Fuel",
        "year": "Year",
        "operating_time": "Operating Time (hrs)",
        "gross_load": "Gross Load (MWh)",
        "heat_input": "Heat Input (MMBtu)",
        "co2_mass": "CO2 Mass (short tons)",
        "so2_mass": "SO2 Mass (short tons)",
        "nox_mass": "NOx Mass (short tons)",
        "dataset_name": "Dataset",
    }
    df = pd.DataFrame(ft["rows"], columns=list(columns)).rename(columns=columns)
    return Response(
        df.to_csv(index=False),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=epadata_text_search.csv"},
    )


# ---- the page and the filter form: unchanged except for the NEW lines in index() ----


@bp.route("/")
def index():
    # NEW: "?q=" is the full-text search box. It never touches the filter form below it.
    # htmx gets just the results; a reload, a shared link or the back button gets the whole
    # page with the results filled in.
    page_data = {}
    if "q" in request.args:
        page_data["ft"] = fulltext.search(
            request.args.get("q", ""),
            page=request.args.get("page", 1, type=int),
            relax=request.args.get("relax") == "1",
        )
        if wants_fragment():
            return render_template(
                "fragments/search/fulltext_results.html", ft=page_data["ft"]
            )

    client = CAMPDClient()

    return render_template(
        "retrieve.html",
        state_codes=client.get_state_codes(),
        fuel_type_codes=client.get_fuel_type_codes(),
        unit_type_codes=client.get_unit_type_codes(),
        control_codes=client.get_control_codes(),
        source_categories=SOURCE_CATEGORIES,  # NEW
        **page_data,  # NEW: adds "ft" only when a search was run
    )


@bp.post("/filter_facilities")
def filter_facilities():
    client = CAMPDClient()

    return render_template(
        "fragments/search/facility_options_filter.html",
        facility_codes=client.search_facilities(**request.form),
    )


# NEW: the EPA accepts exactly these source categories (from its facilities API documentation);
# there is no endpoint that lists them, so the form's drop-down is filled from this list.
SOURCE_CATEGORIES = [
    "Automotive Stampings", "Bulk Industrial Chemical", "Cement Manufacturing", "Cogeneration",
    "Electric Utility", "Industrial Boiler", "Industrial Turbine", "Institutional",
    "Iron & Steel", "Municipal Waste Combustor", "Pulp & Paper Mill", "Petroleum Refinery",
    "Portland Cement Plant", "Small Power Producer", "Theme Park",
]

# NEW: how each filter is named in the form, and what to call it on screen
FILTER_LABELS = {
    "state_code": "state",
    "facility_id": "facility",
    "fuel_code": "fuel",
    "unit_type": "unit type",
    "control_technology": "control technology",
    "source_category": "source category",
}

# CHANGED: columns of the table shown after a retrieval (database column -> heading)
RESULT_COLUMNS = {
    "facility_name": "Facility Name",
    "facility_id": "Facility ID",
    "state": "State",
    "county": "County",
    "unit_id": "Unit ID",
    "unit_type": "Unit Type",
    "primary_fuel": "Primary Fuel",
    "year": "Year",
    "operating_time": "Operating Time (hrs)",
    "gross_load": "Gross Load (MWh)",
    "heat_input": "Heat Input (MMBtu)",
    "co2_mass": "CO2 Mass (short tons)",
    "so2_mass": "SO2 Mass (short tons)",
    "nox_mass": "NOx Mass (short tons)",
    "dataset_name": "Dataset",
}


MEASURES = {"operating_time", "gross_load", "heat_input", "co2_mass", "so2_mass", "nox_mass"}


def _saved_table(session, annual, years, filters):
    """NEW: every record of this retrieval, read back from the database, as a table.

    This includes records that were already saved by an earlier retrieval, so repeating a
    retrieval still shows all of its data (the importer skips what it already has).
    Only the unit-years the EPA sent are listed; if a unit-year was saved more than once
    with different values, the newest version is shown.
    """
    wanted = set()
    for row in annual:
        try:
            wanted.add((int(row["facilityId"]), str(row["unitId"]).strip(), int(row["year"])))
        except (KeyError, TypeError, ValueError):
            pass

    query = (
        select(
            Facility.name.label("facility_name"),
            Facility.id.label("facility_id"),
            Facility.state,
            Facility.county,
            Unit.epa_unit_id.label("unit_id"),
            Unit.type.label("unit_type"),
            Unit.primary_fuel,
            AnnualRecord.reporting_year.label("year"),
            AnnualRecord.operating_time,
            AnnualRecord.gross_load,
            AnnualRecord.heat_input,
            AnnualRecord.co2_mass,
            AnnualRecord.so2_mass,
            AnnualRecord.nox_mass,
            AnnualRecord.dataset_id,
            Dataset.name.label("dataset_name"),
        )
        .join_from(AnnualRecord, Unit, AnnualRecord.unit_key == Unit.internal_id)
        .join(Facility, Unit.facility_id == Facility.id)
        .join(Dataset, Dataset.id == AnnualRecord.dataset_id)
        .where(AnnualRecord.reporting_year.in_(years))
        .order_by(Facility.name, Unit.epa_unit_id, AnnualRecord.reporting_year,
                  AnnualRecord.dataset_id.desc())
    )
    if filters["state_code"]:
        query = query.where(Facility.state == filters["state_code"])
    if filters["facility_id"] and str(filters["facility_id"]).isdigit():
        query = query.where(Facility.id == int(filters["facility_id"]))

    rows, seen = [], set()
    for r in session.execute(query).mappings():
        key = (r["facility_id"], r["unit_id"], r["year"])
        if key in wanted and key not in seen:  # newest version first, because of the ORDER BY
            seen.add(key)
            rows.append(dict(r))
    # Everything becomes text for the table. A missing number stays blank, never 0.
    def show(column, value):
        if value is None:
            return ""
        if column in MEASURES:
            return f"{value:.2f}"  # no thousands commas, so the table sorts it as a number
        return str(value)

    return pd.DataFrame(
        [{RESULT_COLUMNS[c]: show(c, r[c]) for c in RESULT_COLUMNS} for r in rows],
        columns=list(RESULT_COLUMNS.values()),
    )


def _unit_key(row, with_year=True):
    key = (int(row["facilityId"]), str(row["unitId"]).strip())
    return (*key, int(row["year"])) if with_year else key


def _keep_only_filtered_units(annual, attributes, filters):
    """NEW: apply unit type / control technology / source category exactly.

    The EPA's facility-attributes service understands all three filters, so the units it
    returns are the ones that match. The emissions rows are then narrowed to those units,
    which also covers source category (the emissions service is not documented to take it).
    """
    if not (filters["unit_type"] or filters["control_technology"] or filters["source_category"]):
        return annual  # nothing to narrow: keep exactly what the EPA sent
    with_year = all("year" in a for a in attributes)
    allowed = set()
    for a in attributes:
        try:
            allowed.add(_unit_key(a, with_year))
        except (KeyError, TypeError, ValueError):
            pass
    kept = []
    for row in annual:
        try:
            if _unit_key(row, with_year) in allowed:
                kept.append(row)
        except (KeyError, TypeError, ValueError):
            kept.append(row)  # never hide a row we cannot judge: the importer will reject and report it
    return kept


@bp.post("/retrieve_data")
def retrieve_data():
    # CHANGED: this used to show only the first 100 rows the EPA sent and saved nothing.
    # Now it (1) asks the EPA for EVERY page, including the annual emissions, (2) saves the
    # result in the database (so the full-text search and the Dataset Explorer see it), and
    # (3) shows every record, read back from the database.
    years = parse_years(request.form.get("years", ""))
    if not years:
        return render_template(
            "fragments/retrieve_status.html",
            error="Enter a year or range, such as 2015 or 2019-2022.",
        )

    # CHANGED: unit type, control technology and source category are now used too.
    # (They were on the form before but ignored.)
    filters = {
        "state_code": request.form.get("stateCode") or None,
        "facility_id": request.form.get("facilityId") or None,
        "fuel_code": request.form.get("unitFuelType") or None,
        "unit_type": request.form.get("unitType") or None,
        "control_technology": request.form.get("controlTechnology") or None,
        "source_category": request.form.get("sourceCategory") or None,
    }

    # 1) Ask the EPA. Only the exception TYPE is shown: its message can contain the request URL.
    try:
        client = CAMPDClient()
        annual = client.get_annual_emissions(years, **filters)
        attributes = client.get_facility_attributes(years, **filters)
        annual = _keep_only_filtered_units(annual, attributes, filters)
    except Exception as e:
        return render_template(
            "fragments/retrieve_status.html",
            error=f"The EPA request failed ({type(e).__name__}). Check the filters and try again.",
        )

    # 2) Save to the database, then read back what is stored.
    chosen = {FILTER_LABELS[k]: v for k, v in filters.items() if v}  # only the filters used
    label = ", ".join(f"{k}={v}" for k, v in chosen.items()) or "all"
    name = f"EPA CAMPD {years[0]}-{years[-1]} ({label}) {datetime.now():%Y-%m-%d %H:%M:%S}"
    table = None
    with Session() as session:
        summary = import_annual_data(
            session, annual, attributes, name=name, years=years,
            # NEW: the query is kept in its own field (dataset.query_params), not just in the name
            query_params={"years": years, **{k: v for k, v in filters.items() if v}},
        )
        if summary["raw"]:
            table = _saved_table(session, annual, years, filters)

    return render_template(
        "fragments/retrieve_status.html",
        summary=summary,
        table=table,
        years=years,
        filters=filters,
    )
from flask import Blueprint, Response, render_template, request  # CHANGED: + Response

import pandas as pd  # NEW: for the CSV download

from epadata import search as fulltext  # NEW: full-text search (Whoosh)
from epadata.campd import CAMPDClient

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
        **page_data,  # NEW: adds "ft" only when a search was run
    )


@bp.post("/filter_facilities")
def filter_facilities():
    client = CAMPDClient()

    return render_template(
        "fragments/search/facility_options_filter.html",
        facility_codes=client.search_facilities(**request.form),
    )


@bp.post("/retrieve_data")
def retrieve_data():
    client = CAMPDClient()
    df = client.get_facility_data(
        years=request.form["years"],
        facility_id=request.form.get("facilityId", None),
        state_code=request.form.get("stateCode", None),
        fuel_code=request.form.get("unitFuelType", None),
    )

    return render_template("fragments/dataset_table.html", data=df.fillna("N/A"))
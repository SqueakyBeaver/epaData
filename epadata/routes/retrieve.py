from flask import Blueprint, render_template, request

from epadata.campd import CAMPDClient

bp = Blueprint("retrieve", __name__, url_prefix="/retrieve")


@bp.route("/")
def index():
    client = CAMPDClient()

    return render_template(
        "retrieve.html",
        state_codes=client.get_state_codes(),
        fuel_type_codes=client.get_fuel_type_codes(),
        unit_type_codes=client.get_unit_type_codes(),
        control_codes=client.get_control_codes(),
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

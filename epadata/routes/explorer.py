from flask import Blueprint, render_template, request

from epadata.campd import CAMPDClient

bp = Blueprint("explore", __name__, url_prefix="/explore")


@bp.route("/campd")
def campd():
    client = CAMPDClient()

    return render_template(
        "search_campd.html",
        state_codes=client.get_state_codes(),
        fuel_type_codes=client.get_fuel_type_codes(),
    )


@bp.post("/campd/submit_years")
def submit_years():
    client = CAMPDClient()

    print(request.args)

    return render_template(
        "fragments/search/filters_for_year.html",
        facility_codes=client.get_facilities_for_year("2022"),
    )

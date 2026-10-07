

# TODO: Everything for this. This is the dataset explorer, and should maybe be merged with the campd routes???
from flask import Blueprint

bp = Blueprint("details", __name__, url_prefix="/details")


@bp.route("/<facility_id>/<unit_id>")
def unit(facility_id, unit_id):
    return f"Facility {facility_id}, unit {unit_id}"
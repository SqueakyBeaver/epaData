from flask import Blueprint, render_template

from epadata.campd import CAMPDClient

bp = Blueprint("explore", __name__, url_prefix="/explore")


@bp.route("/campd")
def campd():
    client = CAMPDClient()

    return render_template("search_campd.html", state_codes=client.get_state_codes())

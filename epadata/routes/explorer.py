import pandas as pd
from flask import Blueprint, render_template, request

from epadata.campd import CAMPDClient

bp = Blueprint("explore", __name__, url_prefix="/explore")


@bp.route("/")
def index():
    client = CAMPDClient()

    return render_template(
        "explore.html",
        state_codes=client.get_state_codes(),
        fuel_type_codes=client.get_fuel_type_codes(),
    )


@bp.get("/sample_data")
def sample_data():
    df = pd.read_csv("epadata/data/sampledata.csv")

    return render_template("fragments/dataset_table.html", data=df.fillna("N/A"))


# TODO: Route for getting data from CAMPD (or data that is cached in the DB if it's recent enough I guess)

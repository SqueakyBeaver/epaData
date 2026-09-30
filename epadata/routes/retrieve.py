
from flask import Blueprint, render_template, request

bp = Blueprint("retrieve", __name__, url_prefix="/retrieve")



@bp.route("/", methods=["GET", "POST"])
def index():
    return render_template("retrieve.html")

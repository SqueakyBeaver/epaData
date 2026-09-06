from flask import Blueprint, render_template

bp = Blueprint("index", __name__)


@bp.route("/")
def home_page():
    return render_template("index.html")

@bp.route("/test")
def test_route():
    return render_template("fragments/test.html")

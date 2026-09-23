from flask import Blueprint, render_template, request

from epadata.routes.utils import flash_message

bp = Blueprint("index", __name__)


@bp.route("/")
def home_page():
    return render_template("index.html")


@bp.post("/submit_form")
def submit_form():
    content = request.form.get("content", "No content submitted :(")
    return render_template("fragments/test.html", content=content)


@bp.get("/test_alert")
def test_alert():
    return flash_message("Alert test successful")


# TODO: Add route that returns a list of datasets (or pass it as a parameter to the "index.html" template)

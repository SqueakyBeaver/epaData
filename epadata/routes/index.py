from flask import Blueprint, render_template, request

bp = Blueprint("index", __name__)


@bp.route("/")
def home_page():
    return render_template("index.html")


@bp.post("/submit_form")
def submit_form():
    content = request.form.get("content", "No content submitted :(")
    return render_template("fragments/test.html", content=content)

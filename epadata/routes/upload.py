from flask import Blueprint, render_template

bp = Blueprint("upload", __name__, url_prefix="/upload")


@bp.route("/")
def upload_page():
    return render_template("upload.html")


@bp.post("/upload_dataset")
def upload_dataset_file():
    return render_template("upload.html")

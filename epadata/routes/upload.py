from os import path

from flask import Blueprint, current_app, render_template, request
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from epadata.routes.utils import flash_message

bp = Blueprint("upload", __name__, url_prefix="/upload")


def validate_file(file: FileStorage):
    return str(file.filename).rsplit(".", 1)[1].lower() in ("csv", "xlsx", "xls")


@bp.route("/")
def upload_page():
    return render_template("upload.html")


@bp.post("/upload_dataset")
def upload_dataset_file():
    if "dataset" not in request.files:
        return flash_message("No file part uploaded. This is probably a backend error")

    file = request.files["dataset"]

    if not file.filename:
        return flash_message("No file selected.")

    if not validate_file(file):
        return flash_message("File type must be one of: csv, xlsx, or xls")

    filename = secure_filename(file.filename)
    file.save(path.join(current_app.config["UPLOADS_DIR"], filename))

    # TODO: Process the CSV, validate all of the values (i.e. date range), save to SQLite database
    # Each row of the CSV should probably get converted to an AnnualRecord
    # See https://docs.sqlalchemy.org/en/20/orm/quickstart.html#create-objects-and-persist
    # # for a quick overview on how to create and store objects.
    # # Eventually, we'll convert the raw SQLAlchemy engine we have into a Flask-SQLAlchemy thingy
    # Use the session in the `db` model (see: https://docs.sqlalchemy.org/en/20/orm/session_basics.html):
    # # from epadata.db import Session
    # # with Session() as session:
    # #     stuff
    # #     session.commit()
    # Use flash_message (from the routes.utils module to show status message popups if needed)

    return flash_message("Upload successful!", "success")

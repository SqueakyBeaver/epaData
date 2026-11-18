# NEW FILE (Phase 2): the analysis pages.
#   /analysis/            build an analytical dataset (filters in the URL), see counts + missing values
#   /analysis/download    the same dataset as CSV / Excel, with the metadata that makes it reproducible
#   /analysis/traci       the stored TRACI factors, and where a reviewed factor file is imported
from pathlib import Path

import pandas as pd
from flask import Blueprint, render_template, request, url_for
from sqlalchemy import select

from epadata import dataset_builder, traci
from epadata.db import Session
from epadata.db.models import Dataset
from epadata.routes.utils import table_download

bp = Blueprint("analysis", __name__, url_prefix="/analysis")

# The TRACI 2.2 factors for CO2, SO2 and NOx, made from EPA's own spreadsheet by scripts/extract_traci.py
BUNDLED_TRACI_FILE = Path(__file__).resolve().parent.parent / "data" / "traci_factors.csv"

PREVIEW_ROWS = 50
# the columns shown in the preview table (the download has all of them)
PREVIEW_COLUMNS = [
    ("year", "Year", 0), ("state", "State", None), ("facility_name", "Facility", None),
    ("unit_id", "Unit", None), ("primary_fuel", "Fuel", None),
    ("gross_load", "Gross load (MWh)", 0), ("co2_mass", "CO2 (short tons)", 0),
    ("co2_intensity", "CO2 intensity (short tons/MWh)", 3), ("so2_intensity", "SO2 intensity (short tons/MWh)", 5),
    ("nox_intensity", "NOx intensity (short tons/MWh)", 5), ("heat_rate", "Heat rate (MMBtu/MWh)", 2),
    ("co2_mass_yoy_pct", "CO2 change from previous year (%)", 1),
]


def _cell(value, decimals):
    """Text for a table cell. A missing value is shown as an em dash, never as 0."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if decimals is None:
        return str(value)
    return f"{float(value):,.{decimals}f}"


@bp.get("/")
def index():
    f = dataset_builder.Filters.from_args(request.args)
    result = None
    if request.args:  # the form was submitted (or a shared link was opened)
        with Session() as session:
            df = dataset_builder.build_analysis_table(session, f)
        result = dataset_builder.summarize(df)
        result["preview"] = [
            [_cell(row[col], dec) for col, _, dec in PREVIEW_COLUMNS]
            for _, row in df.head(PREVIEW_ROWS).iterrows()
        ]
        result["shown"] = min(len(df), PREVIEW_ROWS)
    with Session() as session:
        datasets = session.scalars(select(Dataset).order_by(Dataset.id.desc())).all()
        dataset_choices = [(d.id, d.name) for d in datasets]
    query = f.to_query()
    return render_template(
        "analysis.html",
        f=f, form=request.args, result=result,
        filter_text=dataset_builder.describe_filters(f),
        columns=[label for _, label, _ in PREVIEW_COLUMNS],
        dataset_choices=dataset_choices,
        share_url=url_for("analysis.index", _external=True, **query) if query else None,
        csv_url=url_for("analysis.download", **query),
        xlsx_url=url_for("analysis.download", format="xlsx", **query),
        raw_variables=dataset_builder.RAW_VARIABLES, derived_variables=dataset_builder.DERIVED_VARIABLES,
    )


@bp.get("/download")
def download():
    f = dataset_builder.Filters.from_args(request.args)
    with Session() as session:
        df = dataset_builder.build_analysis_table(session, f)
        meta = dataset_builder.export_metadata(
            session, f, df, url=url_for("analysis.index", _external=True, **f.to_query()))
    return table_download(df[dataset_builder.EXPORT_COLUMNS], "epadata-analysis", metadata=meta)


@bp.route("/traci", methods=["GET", "POST"])
def traci_page():
    message = None
    with Session() as session:
        if request.method == "POST" and request.form.get("bundled"):
            # the button "Import the factors included with the project"
            message = traci.import_factors(session, BUNDLED_TRACI_FILE.read_text(encoding="utf-8"))
        elif request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename:
                message = {"ok": False, "errors": ["Choose a .csv file first."]}
            else:
                try:
                    text = upload.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = None
                    message = {"ok": False, "errors": ["The file is not a UTF-8 text/CSV file."]}
                if text is not None:
                    message = traci.import_factors(session, text)
        factors = traci.load_factors(session)
    return render_template(
        "traci.html", message=message, factors=factors.to_dict("records"),
        required_columns=traci.REQUIRED_COLUMNS, short_ton_kg=traci.SHORT_TON_KG,
    )
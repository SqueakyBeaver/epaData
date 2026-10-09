from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, make_response, render_template, request
from sqlalchemy import select
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from epadata import globals
from epadata.db import Session
from epadata.db.models import AnnualRecord, Dataset, Facility, Unit
from epadata.routes.utils import AlertType, flash_message
from epadata.validations import FacilityInfoRecordModel

bp = Blueprint("upload", __name__, url_prefix="/upload")

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
PREVIEW_CACHE_TIMEOUT = 1800  # 30 minutes TTL for staged uploads


def validate_file_type(file: FileStorage) -> bool:
    return bool(
        file.filename and Path(file.filename).suffix.lower() in ALLOWED_EXTENSIONS
    )


def _render_response(
    preview: dict | None = None,
    message: str | None = None,
    alert_type: AlertType = "danger",
    status: int = 200,
    template: str | None = None,
):
    """Renders HTMX fragments or full Jinja pages depending on request headers."""
    if not template:
        is_htmx = request.headers.get("HX-Request") == "true"
        template = "fragments/upload_preview.html" if is_htmx else "upload.html"

    response = make_response(
        render_template(
            template,
            preview=preview,
            message=message,
            message_type=alert_type,
        ),
        status,
    )

    if message:
        response = flash_message(str(message), alert_type, response)

    return response


@bp.route("/")
def upload_page():
    return _render_response()


@bp.post("/upload_dataset")
def upload_dataset_file():
    """Stages a file to disk, parses metadata into Flask-Cache, and returns HTMX preview."""
    file = request.files.get("dataset")
    if file is None:
        return _render_response(message="No file part uploaded.", status=400)
    if not file.filename:
        return _render_response(message="No file selected.", status=400)
    if not validate_file_type(file):
        return _render_response(
            message="File type must be one of: csv, xlsx, or xls", status=400
        )

    token = uuid4().hex
    uploads_dir = Path(current_app.config["UPLOADS_DIR"])
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Save physical file to disk for re-parsing during column mapping steps
    file_path = uploads_dir / f"{token}-{secure_filename(file.filename)}"
    file.save(file_path)

    try:
        prepared = FacilityInfoRecordModel.prepare_file(str(file_path))
    except Exception as error:
        file_path.unlink(missing_ok=True)
        return _render_response(
            message=f"Unable to read this file: {error}", status=400
        )

    autofilled_name = Path(file.filename).stem

    with Session() as session:
        name_exists = (
            session.scalar(select(Dataset.id).where(Dataset.name == autofilled_name))
            is not None
        )

    preview = {
        "token": token,
        "filename": str(file_path),
        "original_filename": file.filename,
        "dataset_name": autofilled_name,
        "name_exists": name_exists,
        **prepared,
    }

    globals.cache.set(f"preview:{token}", preview, timeout=PREVIEW_CACHE_TIMEOUT)

    initial_message = (
        "A dataset with this autofilled name already exists. Please choose another name."
        if name_exists
        else "Review the preview and confirm the import."
    )
    initial_msg_type = "warning" if name_exists else "info"

    return _render_response(
        preview=preview,
        message=initial_message,
        alert_type=initial_msg_type,
    )


@bp.post("/check_name")
def check_dataset_name():
    """Live HTMX endpoint for verifying dataset name uniqueness with HTML5 custom validation."""
    name = request.form.get("dataset_name", "").strip()

    with Session() as session:
        exists = (
            session.scalar(select(Dataset.id).where(Dataset.name == name)) is not None
            if name
            else False
        )

    if not name:
        feedback = (
            '<div class="text-danger small mt-1">Dataset name cannot be blank.</div>'
            '<script>document.getElementById("datasetName").setCustomValidity("Dataset name cannot be blank.");</script>'
        )
    elif exists:
        feedback = (
            '<div class="text-danger small mt-1">'
            '<i class="bi bi-x-circle me-1"></i>A dataset with this name already exists.'
            "</div>"
            '<script>document.getElementById("datasetName").setCustomValidity("A dataset with this name already exists.");</script>'
        )
    else:
        feedback = (
            '<div class="text-success small mt-1">'
            '<i class="bi bi-check-circle me-1"></i>Name is available.'
            "</div>"
            '<script>document.getElementById("datasetName").setCustomValidity("");</script>'
        )

    return feedback


@bp.post("/confirm")
def confirm_upload():
    """Processes mapped data confirmation and imports records into SQLite."""
    token = request.form.get("preview_token", "")
    cache_key = f"preview:{token}"
    preview = globals.cache.get(cache_key)

    if not preview or not Path(preview.get("filename", "")).is_file():
        return _render_response(
            message="This upload preview has expired. Please upload the file again.",
            status=400,
        )

    file_path = Path(preview["filename"])

    # Build column mapping from user selections
    mapping = {}
    unresolved_columns = []
    for index, source in enumerate(preview["columns"]):
        target = request.form.get(f"mapping_{index}", "").strip()
        if target and target != "__ignore__":
            mapping[source] = target
        elif target == "":
            unresolved_columns.append(source)

    if len(mapping) != len(set(mapping.values())):
        return _render_response(
            preview=preview,
            message="Each EPA field can be mapped from only one uploaded column.",
            status=422,
        )

    # Re-validate file on disk using user's custom column mapping
    try:
        prepared = FacilityInfoRecordModel.prepare_file(str(file_path), mapping)
    except Exception as error:
        return _render_response(
            preview=preview,
            message=f"Unable to validate file mapping: {error}",
            status=400,
        )

    preview.update(prepared)
    preview["unmapped_columns"] = unresolved_columns
    preview["dataset_name"] = request.form.get("dataset_name", "").strip()

    if not preview["dataset_name"]:
        return _render_response(
            preview=preview, message="Enter a name for this dataset.", status=422
        )

    if preview["unmapped_columns"]:
        globals.cache.set(cache_key, preview, timeout=PREVIEW_CACHE_TIMEOUT)
        return _render_response(
            preview=preview,
            message=(
                f"{len(preview['invalid_records'])} row(s) need attention. "
                "Map rejected fields or explicitly ignore them before importing."
            ),
            status=422,
        )

    if not preview["valid_records"]:
        return _render_response(
            preview=preview, message="No valid records remain to import.", status=422
        )

    with Session() as session:
        if session.scalar(
            select(Dataset.id).where(Dataset.name == preview["dataset_name"])
        ):
            return _render_response(
                preview=preview,
                message="A dataset with that name already exists.",
                status=409,
            )
        _import_rows(session, preview)

    # Cleanup temporary file from disk & cache entry
    file_path.unlink(missing_ok=True)
    globals.cache.delete(cache_key)

    success_msg = (
        f"Dataset '{preview['dataset_name']}' uploaded successfully with "
        f"{len(preview['valid_records'])} accepted row(s) and "
        f"{len(preview['invalid_records'])} rejected row(s)."
    )

    if request.headers.get("HX-Request") == "true":
        return (
            f'<div id="alert-container" hx-swap-oob="true">'
            f'<div class="alert alert-success alert-dismissible fade show" role="alert">'
            f"{success_msg}"
            f'<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>'
            f"</div>"
            f"</div>"
            f'<div class="d-none"></div>'  # Replaces and clears content inside #output
        )

    return _render_response(
        preview=None, message=success_msg, alert_type="success", template="upload.html"
    )


def _import_rows(session, preview: dict) -> None:
    valid_rows = preview["valid_records"]
    dataset = Dataset(
        name=preview["dataset_name"],
        data_source="User Upload",
        reporting_years=sorted({row.reporting_year for row in valid_rows}),
        original_filename=preview["original_filename"],
        raw_records_cnt=preview["row_count"],
        accepted_records_cnt=len(valid_rows),
    )
    session.add(dataset)
    session.flush()

    discovered_facilities: dict[int, Facility] = {}
    discovered_units: dict[tuple[int, str], Unit] = {}

    for row in valid_rows:
        if row.facility_id not in discovered_facilities:
            facility = session.get(Facility, row.facility_id)
            if facility is None:
                facility = Facility(
                    id=row.facility_id,
                    name=row.facility_name,
                    state=row.state,
                    county=row.county,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    source_category=row.source_category,
                )
                session.add(facility)
            discovered_facilities[row.facility_id] = facility

        unit_key = (row.facility_id, row.unit_id)
        if unit_key not in discovered_units:
            unit = session.scalar(
                select(Unit).where(
                    Unit.facility_id == row.facility_id,
                    Unit.epa_unit_id == row.unit_id,
                )
            )
            if unit is None:
                unit = Unit(
                    facility_id=row.facility_id,
                    epa_unit_id=row.unit_id,
                    primary_fuel=row.primary_fuel_type,
                    secondary_fuel=row.secondary_fuel_type,
                    operating_date=row.commercial_operation_date,
                    retirement_date=None,
                )
                session.add(unit)
                session.flush()
            discovered_units[unit_key] = unit

        session.add(
            AnnualRecord(
                dataset_id=dataset.id,
                facility_id=discovered_facilities[row.facility_id].id,
                unit_key=discovered_units[unit_key].internal_id,
                reporting_year=row.reporting_year,
                operating_time=row.operating_time,
                so2_controls=row.so2_controls,
                nox_controls=row.nox_controls,
                pm_controls=row.pm_controls,
                program_code=row.program_code,
                gross_load=row.gross_load,
                steam_load=row.steam_load,
                heat_input=row.heat_input,
                co2_mass=row.co2_mass,
                so2_mass=row.so2_mass,
                nox_mass=row.nox_mass,
            )
        )
    session.commit()

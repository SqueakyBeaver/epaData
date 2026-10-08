from datetime import datetime
from os import path

from flask import Blueprint, current_app, render_template, request
from sqlalchemy import select
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from epadata.db import Session
from epadata.db.models import AnnualRecord, Dataset, Facility, Unit
from epadata.routes.utils import flash_message
from epadata.validations import FacilityInfoRecordModel

bp = Blueprint("upload", __name__, url_prefix="/upload")


def validate_file_type(file: FileStorage):
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

    if not validate_file_type(file):
        return flash_message("File type must be one of: csv, xlsx, or xls")

    filename = path.join(
        current_app.config["UPLOADS_DIR"], secure_filename(file.filename)
    )
    file.save(filename)

    try:
        valid_rows, invalid_rows = FacilityInfoRecordModel.validate_from_file(filename)

    except Exception as e:
        return flash_message(
            f"An error occured while validating the file:\n{e}", "danger"
        )

    # Sylva: Pull this out into a function. This level of indents is blasphemy.
    with Session() as session:
        dataset = Dataset(
            name=f"{path.basename(filename)}_{datetime.now()}",
            data_source="User Upload",
            reporting_years=sorted({row.reporting_year for row in valid_rows}),
            original_filename=file.filename,
            raw_records_cnt=len(valid_rows) + len(invalid_rows),
            accepted_records_cnt=len(valid_rows),
        )
        session.add(dataset)
        session.flush()

        discovered_facilities: dict[int, Facility] = {}
        discovered_units: dict[tuple[int, str], Unit] = {}  # (facility_id, unit_id)

        for row in valid_rows:
            facility: Facility | None = None
            unit: Unit | None = None
            if not row.facility_id in discovered_facilities:
                stmt = select(Facility).where(Facility.id == row.facility_id)
                facility = session.execute(stmt).scalar()

                if not facility:
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

            if not (row.facility_id, row.unit_id) in discovered_units:
                stmt = (
                    select(Unit)
                    .where(Unit.facility_id == row.facility_id)
                    .where(Unit.epa_unit_id == row.unit_id)
                )
                unit = session.execute(stmt).scalar()

                if not unit:
                    unit = Unit(
                        facility_id=row.facility_id,
                        epa_unit_id=row.unit_id,
                        primary_fuel=row.primary_fuel_type,
                        secondary_fuel=row.secondary_fuel_type,
                        operating_date=row.commercial_operation_date,
                        retirement_date=None,  # This is not in the bulk data format for facility info
                    )

                    session.add(unit)
                    session.flush()

                discovered_units[(row.facility_id, row.unit_id)] = unit

            session.add(
                AnnualRecord(
                    dataset_id=dataset.id,
                    facility_id=discovered_facilities[row.facility_id].id,
                    unit_key=discovered_units[
                        (row.facility_id, row.unit_id)
                    ].internal_id,
                    reporting_year=row.reporting_year,
                    operating_time=row.operating_time,
                    so2_controls=row.so2_controls,
                    nox_controls=row.nox_controls,
                    pm_controls=row.pm_controls,
                    program_code=row.program_code,
                    # Note: These aren't in the facility information data; they're in emissions data
                    gross_load=row.gross_load,
                    steam_load=row.steam_load,
                    heat_input=row.heat_input,
                    co2_mass=row.co2_mass,
                    so2_mass=row.so2_mass,
                    nox_mass=row.nox_mass,
                )
            )

        session.commit()

    return flash_message("Upload successful!", "success")

# this file will import the actual data from API into our database, in order for us to do calls. will have to update the search_campd.html #
"""Turns CAMPD API rows into rows in our database.

Two inputs, both lists of dicts straight from the API:
  * annual_rows    - one dict per unit per year, WITH the emissions numbers
  * attribute_rows - one dict per unit per year, WITH county / lat / long /
                     commercial operation date (used to fill in extra detail)

Duplicate rule (unit + year already stored from an EARLIER dataset):
  * same values      -> skipped and counted (nothing new to store)
  * different values -> saved as a NEW VERSION in this dataset, and listed
  * nothing is ever overwritten
"""
from collections import Counter
from datetime import date
from math import isclose, isnan

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from epadata.db.models import AnnualRecord, Dataset, Facility, Unit

# database column:  field name in the API's annual emissions rows
MEASUREMENTS = {
    # hours (NOT countOpTime, which is a row count)
    "operating_time": "sumOpTime",
    "gross_load": "grossLoad",
    "steam_load": "steamLoad",
    "heat_input": "heatInput",
    "co2_mass": "co2Mass",
    "so2_mass": "so2Mass",
    "nox_mass": "noxMass",
}
TEXT_FIELDS = {
    "so2_controls": "so2ControlInfo",
    "nox_controls": "noxControlInfo",
    "pm_controls": "pmControlInfo",
    "program_code": "programCodeInfo",
}

MAX_NOTES = 50  # how many lines of each kind are kept in Dataset.notes


class RowRejected(Exception):
    """This API row is unusable; skip it and say why."""


# ---- small cleaning helpers -------------------------------------------------
def is_blank(v):
    return v is None or (isinstance(v, float) and isnan(v)) or (isinstance(v, str) and not v.strip())


def text(v):
    return None if is_blank(v) else str(v).strip()


def number(v):
    """Blank -> None (NOT zero). Junk like 'abc' raises ValueError."""
    return None if is_blank(v) else float(v)


def to_date(v):
    return None if is_blank(v) else date.fromisoformat(str(v).strip()[:10])


def parse_years(raw: str) -> list[int]:
    """'2015, 2019-2022' -> [2015, 2019, 2020, 2021, 2022]"""
    years = set()
    for part in raw.replace(" ", "").split(","):
        start, _, end = part.partition("-")
        if start.isdigit() and end.isdigit():
            years.update(range(int(start), int(end) + 1))
        elif start.isdigit() and not end:
            years.add(int(start))
    return sorted(years)


#  compares two stored/incoming values. Blank equals blank, and numbers that
# differ only by floating-point noise count as equal.
def same_value(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def import_annual_data(session, annual_rows, attribute_rows, *, name, years,
                       data_source="EPA CAMPD API", query_params=None):
    """Save the rows and return a summary dict. Commits once at the end.

    query_params (NEW): the filters of the retrieval, saved in dataset.query_params.

    received = accepted + skipped_identical + rejected
    """
    if not annual_rows:
        return {"dataset_id": None, "raw": 0, "accepted": 0, "rejected": 0, "rejects": []}

    attrs = {}
    for a in attribute_rows:
        try:
            attrs.setdefault((int(a["facilityId"]), text(a["unitId"])), a)
        except (KeyError, TypeError, ValueError):
            pass

    unique_name, n = name, 2
    while session.scalar(select(Dataset.id).where(Dataset.name == unique_name)):
        unique_name, n = f"{name} ({n})", n + 1

    dataset = Dataset(
        name=unique_name, data_source=data_source,
        reporting_years=sorted(years), raw_records_cnt=len(annual_rows),
        query_params=query_params
    )
    session.add(dataset)
    session.flush()

    fac_cache = {f.id: f for f in session.scalars(select(Facility)).all()}
    unit_cache = {(u.facility_id, u.epa_unit_id)                  : u for u in session.scalars(select(Unit)).all()}

    existing_records = {}
    for rec in session.scalars(
        select(AnnualRecord).order_by(
            AnnualRecord.dataset_id.desc(), AnnualRecord.id.desc())
    ).all():
        existing_records.setdefault(
            (rec.unit_key, rec.reporting_year), []).append(rec)

    totals = Counter()
    rejects, version_notes = [], []
    seen_in_import = set()
    new_facilities, new_units, new_records = [], [], []

    for i, row in enumerate(annual_rows, start=1):
        try:
            try:
                facility_id, year = int(row["facilityId"]), int(row["year"])
            except (KeyError, TypeError, ValueError):
                raise RowRejected("missing or invalid facilityId / year")

            epa_unit_id = text(row.get("unitId"))
            f_name, state = text(row.get("facilityName")
                                 ), text(row.get("stateCode"))
            if not epa_unit_id:
                raise RowRejected("missing unitId")
            if not f_name or not state:
                raise RowRejected("missing facility name or state")

            extra = attrs.get((facility_id, epa_unit_id), {})

            # Resolve or create Facility
            facility = fac_cache.get(facility_id)
            if facility is None:
                facility = Facility(id=facility_id, name=f_name, state=state)
                new_facilities.append(facility)
                fac_cache[facility_id] = facility
                totals["facilities_added"] += 1
            facility.name, facility.state = f_name, state

            if county := text(extra.get("county")):
                facility.county = county
            if cat := text(extra.get("sourceCategory")):
                facility.source_category = cat
            if lat := number(extra.get("latitude")):
                facility.latitude = lat
            if lon := number(extra.get("longitude")):
                facility.longitude = lon

            # Resolve or create Unit
            ukey = (facility_id, epa_unit_id)
            unit = unit_cache.get(ukey)
            if unit is None:
                unit = Unit(facility_id=facility_id, epa_unit_id=epa_unit_id)
                new_units.append(unit)
                unit_cache[ukey] = unit
                totals["units_added"] += 1

            unit.type = text(row.get("unitType")) or text(
                extra.get("unitType"))
            unit.primary_fuel = text(row.get("primaryFuelInfo")) or text(
                extra.get("primaryFuelInfo"))
            unit.secondary_fuel = text(row.get("secondaryFuelInfo")) or text(
                extra.get("secondaryFuelInfo"))
            if op_date := to_date(extra.get("commercialOperationDate")):
                unit.operating_date = op_date

        except (RowRejected, ValueError, TypeError) as e:
            rejects.append(f"row {i}: {e}")
        except IntegrityError as e:
            rejects.append(f"row {i}: {str(e.orig)}")
        else:
            continue

    if new_facilities:
        session.add_all(new_facilities)
    if new_units:
        session.add_all(new_units)
    session.flush()

    for u in new_units:
        unit_cache[(u.facility_id, u.epa_unit_id)] = u

    # Process annual emission records
    for i, row in enumerate(annual_rows, start=1):
        try:
            try:
                facility_id, year = int(row["facilityId"]), int(row["year"])
            except (KeyError, TypeError, ValueError):
                continue

            epa_unit_id = text(row.get("unitId"))
            unit = unit_cache.get((facility_id, epa_unit_id))
            if not unit or unit.internal_id is None:
                continue

            values = {col: number(row.get(field))
                      for col, field in MEASUREMENTS.items()}
            values.update({col: text(row.get(field))
                          for col, field in TEXT_FIELDS.items()})

            import_key = (unit.internal_id, year)
            if import_key in seen_in_import:
                raise RowRejected(
                    f"duplicate within this import: facility {facility_id}, unit {epa_unit_id}, year {year}"
                )
            seen_in_import.add(import_key)

            versions = existing_records.get(import_key, [])
            latest = versions[0] if versions else None

            if latest and all(same_value(getattr(latest, col), v) for col, v in values.items()):
                totals["skipped_identical"] += 1
                continue

            new_rec = AnnualRecord(
                dataset_id=dataset.id, facility_id=facility_id,
                unit_key=unit.internal_id, reporting_year=year, **values
            )
            new_records.append(new_rec)

            if latest is None:
                totals["records_inserted"] += 1
            else:
                totals["new_versions"] += 1
                changes = [f"{col}: {getattr(latest, col)} -> {v}" for col,
                           v in values.items() if not same_value(getattr(latest, col), v)]
                version_notes.append(
                    f"row {i}: facility {facility_id}, unit {epa_unit_id}, year {year} differs from dataset {latest.dataset_id}: " + "; ".join(changes[:6]))

            existing_records.setdefault(import_key, []).insert(0, new_rec)

        except RowRejected as e:
            # Avoid duplicating rejection logs if already caught in pass 1, or log new ones here
            if f"row {i}:" not in "".join(rejects):
                rejects.append(f"row {i}: {e}")

    if new_records:
        session.add_all(new_records)

    accepted = totals["records_inserted"] + totals["new_versions"]
    dataset.accepted_records_cnt = accepted

    # the saved notes now hold three sections instead of only rejects.#
    lines = []
    if rejects:
        lines.append("REJECTED ROWS:")
        lines += rejects[:MAX_NOTES]
        if len(rejects) > MAX_NOTES:
            lines.append(f"... and {len(rejects) - MAX_NOTES} more")
    if version_notes:
        lines.append("NEW VERSIONS (values differ from data already stored):")
        lines += version_notes[:MAX_NOTES]
        if len(version_notes) > MAX_NOTES:
            lines.append(f"... and {len(version_notes) - MAX_NOTES} more")
    if totals["skipped_identical"]:
        lines.append(
            f"{totals['skipped_identical']} records were identical to stored data and skipped.")
    dataset.notes = "\n".join(lines) or None

    try:
        session.commit()
    except IntegrityError as e:
        session.rollback()
        return {"dataset_id": None, "raw": len(annual_rows), "accepted": 0, "rejected": len(annual_rows), "rejects": [str(e.orig)]}

    return {
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "raw": len(annual_rows),
        "accepted": accepted,
        "rejected": len(rejects),
        "rejects": rejects,
        "version_notes": version_notes,
        **{k: totals[k] for k in ("facilities_added", "units_added", "records_inserted", "new_versions", "skipped_identical")}
    }

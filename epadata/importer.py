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
    "operating_time": "sumOpTime",  # hours (NOT countOpTime, which is a row count)
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


# ---- saving one API row -----------------------------------------------------
#  takes the row number `i` and a `version_notes` list so it can describe changes.
def _save_row(session, dataset, row, attrs, fac_cache, unit_cache, created, i, version_notes):
    try:
        facility_id, year = int(row["facilityId"]), int(row["year"])
    except (KeyError, TypeError, ValueError):
        raise RowRejected("missing or invalid facilityId / year")
    epa_unit_id = text(row.get("unitId"))
    name, state = text(row.get("facilityName")), text(row.get("stateCode"))
    if not epa_unit_id:
        raise RowRejected("missing unitId")
    if not name or not state:
        raise RowRejected("missing facility name or state")

    extra = attrs.get((facility_id, epa_unit_id), {})
    done = Counter()

    # 1) Facility: find it by EPA ID, or create it.
    facility = fac_cache.get(facility_id) or session.get(Facility, facility_id)
    if facility is None:
        facility = Facility(id=facility_id, name=name, state=state)
        session.add(facility)
        created.append((fac_cache, facility_id))
        done["facilities_added"] += 1
    facility.name, facility.state = name, state
    fac_cache[facility_id] = facility
    if text(extra.get("county")):
        facility.county = text(extra["county"])
    if text(extra.get("sourceCategory")):
        facility.source_category = text(extra["sourceCategory"])
    if number(extra.get("latitude")) is not None:
        facility.latitude = number(extra["latitude"])
    if number(extra.get("longitude")) is not None:
        facility.longitude = number(extra["longitude"])

    # 2) Unit: unique per (facility, EPA unit ID).
    ukey = (facility_id, epa_unit_id)
    unit = unit_cache.get(ukey) or session.scalar(
        select(Unit).where(Unit.facility_id == facility_id, Unit.epa_unit_id == epa_unit_id)
    )
    if unit is None:
        unit = Unit(facility_id=facility_id, epa_unit_id=epa_unit_id)
        session.add(unit)
        created.append((unit_cache, ukey))
        done["units_added"] += 1
    unit_cache[ukey] = unit
    unit.type = text(row.get("unitType")) or text(extra.get("unitType"))
    unit.primary_fuel = text(row.get("primaryFuelInfo")) or text(extra.get("primaryFuelInfo"))
    unit.secondary_fuel = text(row.get("secondaryFuelInfo")) or text(extra.get("secondaryFuelInfo"))
    op_date = to_date(extra.get("commercialOperationDate"))
    if op_date:
        unit.operating_date = op_date

    session.flush()  # gives a brand-new unit its internal_id, which we need next

    # 3) Annual record.
    values = {col: number(row.get(field)) for col, field in MEASUREMENTS.items()}
    values.update({col: text(row.get(field)) for col, field in TEXT_FIELDS.items()})

    #  look at EVERY stored version of this unit-year (newest first),
    # instead of finding one record and overwriting it.
    versions = session.scalars(
        select(AnnualRecord)
        .where(AnnualRecord.unit_key == unit.internal_id, AnnualRecord.reporting_year == year)
        .order_by(AnnualRecord.dataset_id.desc(), AnnualRecord.id.desc())
    ).all()

    # NEW: the same unit-year twice inside ONE import is a data-quality problem.
    # Reject the later row and say so; the database also forbids it.
    if any(v.dataset_id == dataset.id for v in versions):
        raise RowRejected(
            f"duplicate within this import: facility {facility_id}, unit {epa_unit_id}, year {year} "
            "already appeared earlier in the same file"
        )

    latest = versions[0] if versions else None  # the version searches will show

    # new: identical to what is already stored so then nothing to add. Counted, and never hidden.
    if latest is not None and all(same_value(getattr(latest, col), v) for col, v in values.items()):
        done["skipped_identical"] += 1
        return done

    session.add(AnnualRecord(dataset_id=dataset.id, facility_id=facility_id,
                             unit_key=unit.internal_id, reporting_year=year, **values))
    if latest is None:
        done["records_inserted"] += 1  # first time we have seen this unit-year
    else:
        # new: values differ -> keep the old version, store this one beside it, and list the differences.
        done["new_versions"] += 1
        changes = [f"{col} {getattr(latest, col)} -> {v}" for col, v in values.items()
                   if not same_value(getattr(latest, col), v)]
        version_notes.append(
            f"row {i}: facility {facility_id}, unit {epa_unit_id}, year {year} differs from "
            f"dataset {latest.dataset_id}: " + "; ".join(changes[:6])
        )

    session.flush()  # the database's CHECK / UNIQUE / FOREIGN KEY rules run HERE
    return done


# ---- the public function ----------------------------------------------------
def import_annual_data(session, annual_rows, attribute_rows, *, name, years,
                       data_source="EPA CAMPD API"):
    """Save the rows and return a summary dict. Commits once at the end.

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
    dataset = Dataset(name=unique_name, data_source=data_source,
                      reporting_years=sorted(years), raw_records_cnt=len(annual_rows))
    session.add(dataset)
    session.flush()

    fac_cache, unit_cache = {}, {}
    totals, rejects, version_notes = Counter(), [], []
    for i, row in enumerate(annual_rows, start=1):
        created = []
        try:
            # A "savepoint": if THIS row breaks a rule, only this row is undone.
            with session.begin_nested():
                totals.update(_save_row(session, dataset, row, attrs, fac_cache,
                                        unit_cache, created, i, version_notes))
        except (RowRejected, ValueError, TypeError) as e:
            rejects.append(f"row {i}: {e}")
        except IntegrityError as e:
            rejects.append(f"row {i}: {str(e.orig)}")
        else:
            continue
        for cache, key in created:  # forget things created by the row that failed
            cache.pop(key, None)

    #  "accepted" = records actually stored by this import (first-time + new versions)#
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
        lines.append(f"{totals['skipped_identical']} records were identical to stored data and skipped.")
    dataset.notes = "\n".join(lines) or None
    session.commit()

    return {"dataset_id": dataset.id, "dataset_name": dataset.name, "raw": len(annual_rows),
            "accepted": accepted, "rejected": len(rejects), "rejects": rejects,
            "version_notes": version_notes,  # NEW
            **{k: totals[k] for k in ("facilities_added", "units_added", "records_inserted",
                                      "new_versions", "skipped_identical")}}  # CHANGED: records_updated is gone
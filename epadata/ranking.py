"""Top-N / Bottom-N ("ranking") search over the stored data.

Examples this answers :
    Top 10 facilities by CO2 emissions
    Top 20 units by gross generation
    Top 10 units by heat input
    Units with the lowest SO2 emissions        (Bottom-N)
    Top CO2-emitting facility in each state    (group and rank)
    Top 10 coal units in Kentucky              (ranking with filters)

"""

from sqlalchemy import func, select

from epadata.db import Session
from epadata.db.models import AnnualRecord, Facility, Unit

MAX_N = 1000
DEFAULT_N = 10
PER_PAGE = 25

# key used in the URL -> (name shown, unit shown, database column)
METRICS = {
    "co2_mass": ("CO₂ mass", "short tons", AnnualRecord.co2_mass),
    "so2_mass": ("SO₂ mass", "short tons", AnnualRecord.so2_mass),
    "nox_mass": ("NOx mass", "short tons", AnnualRecord.nox_mass),
    "heat_input": ("Heat input", "MMBtu", AnnualRecord.heat_input),
    "gross_load": ("Gross load", "MWh", AnnualRecord.gross_load),
    "steam_load": ("Steam load", "1000 lb", AnnualRecord.steam_load),
    "operating_time": ("Operating time", "hours", AnnualRecord.operating_time),
}


def _years(text: str) -> list[int]:
    """'2015, 2019-2022' -> [2015, 2019, 2020, 2021, 2022]. Only sensible years (1900-2100)."""
    years = set()
    for part in text.replace(" ", "").split(","):
        start, _, end = part.partition("-")
        if start.isdigit() and len(start) == 4 and (not end or (end.isdigit() and len(end) == 4)):
            low, high = int(start), int(end or start)
            years.update(y for y in range(min(low, high), max(low, high) + 1) if 1900 <= y <= 2100)
    return sorted(years)


def _clean_args(args) -> dict:
    """Only the parameters this search understands, tidied. This is also the saved URL."""
    out = {"rank_by": args.get("rank_by", "").strip()}
    out["dir"] = "asc" if args.get("dir") == "asc" else "desc"
    try:
        n = int(args.get("n", DEFAULT_N))
    except (TypeError, ValueError):
        n = DEFAULT_N
    out["n"] = min(max(n, 1), MAX_N)
    out["level"] = "facility" if args.get("level") == "facility" else "unit"
    out["group"] = "state" if args.get("group") == "state" else ""
    for key in ("years", "state", "fuel", "unit_type", "facility"):
        out[key] = (args.get(key) or "").strip()[:100]
    return {k: v for k, v in out.items() if v != ""}


def _title(a: dict) -> str:
    label, unit, _ = METRICS[a["rank_by"]]
    n = a["n"]
    what = ("facility" if n == 1 else "facilities") if a.get("level") == "facility" else ("unit" if n == 1 else "units")
    if a.get("group") == "state":
        text = f"{'Top' if a['dir'] == 'desc' else 'Bottom'} {n} {what} in each state"
    else:
        text = f"{'Top' if a['dir'] == 'desc' else 'Bottom'} {n} {what}"
    text += f" by {label} ({unit})"
    extra = []
    if a.get("years"):
        extra.append(f"year {a['years']}")
    if a.get("state"):
        extra.append(f"state {a['state'].upper()}")
    if a.get("fuel"):
        extra.append(f"fuel contains “{a['fuel']}”")
    if a.get("unit_type"):
        extra.append(f"unit type contains “{a['unit_type']}”")
    if a.get("facility"):
        extra.append(f"facility “{a['facility']}”")
    return text + (", " + ", ".join(extra) if extra else "")


def _like(text: str) -> str:
    """Text for LIKE: the user's own % and _ are matched literally (escape character is \\)."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _filtered_records(a: dict):
    """The records that pass the filters (newest version of each unit-year only)."""
    newest = select(func.max(AnnualRecord.id)).group_by(
        AnnualRecord.unit_key, AnnualRecord.reporting_year
    )
    where = [AnnualRecord.id.in_(newest)]
    years = _years(a.get("years", ""))
    if a.get("years"):
        where.append(AnnualRecord.reporting_year.in_(years or [-1]))  # unreadable years: no match
    if a.get("state"):
        codes = [s.strip().upper() for s in a["state"].replace(";", ",").split(",") if s.strip()]
        where.append(Facility.state.in_(codes or ["??"]))
    if a.get("fuel"):
        where.append(Unit.primary_fuel.ilike(_like(a["fuel"]), escape="\\"))
    if a.get("unit_type"):
        where.append(Unit.type.ilike(_like(a["unit_type"]), escape="\\"))
    if a.get("facility"):
        text = a["facility"]
        match = Facility.name.ilike(_like(text), escape="\\")
        if text.isdigit() and len(text) <= 15:  # longer numbers do not fit the database's integers
            match = match | (Facility.id == int(text))
        where.append(match)
    return where


def rank(args, page: int = 1, per_page: int | None = PER_PAGE) -> dict:
    """Run a ranking search. Never raises for bad input; see "error".

    Returns: args (cleaned, usable in links), title, rows (dicts, in rank order), total, page,
    pages, level, metric_label, metric_unit, excluded (matching records with no value, so
    not ranked), error.
    """
    a = _clean_args(args)
    out = dict(args=a, title="", rows=[], total=0, page=1, pages=1, level=a["level"],
               metric_label="", metric_unit="", excluded=0, error=None)
    if a.get("rank_by") not in METRICS:
        out["error"] = "Choose what to rank by (for example CO₂ mass)."
        return out
    label, unit, column = METRICS[a["rank_by"]]
    out.update(title=_title(a), metric_label=label, metric_unit=unit)

    where = _filtered_records(a)
    desc = a["dir"] == "desc"
    order_value = column.desc() if desc else column.asc()

    if a["level"] == "facility":
        value = func.sum(column).label("value")
        base = (
            select(
                Facility.id.label("facility_id"),
                Facility.name.label("facility_name"),
                Facility.state.label("state"),
                AnnualRecord.reporting_year.label("year"),
                func.count(AnnualRecord.id).label("units_counted"),
                value,
            )
            .join(Facility, Facility.id == AnnualRecord.facility_id)
            .join(Unit, (Unit.internal_id == AnnualRecord.unit_key)
                  & (Unit.facility_id == AnnualRecord.facility_id))
            .where(*where, column.is_not(None))
            .group_by(Facility.id, Facility.name, Facility.state, AnnualRecord.reporting_year)
        )
        tie_breaks = (Facility.name, AnnualRecord.reporting_year)
        order_inner = (func.sum(column).desc() if desc else func.sum(column).asc())
    else:
        base = (
            select(
                Facility.id.label("facility_id"),
                Facility.name.label("facility_name"),
                Facility.state.label("state"),
                Unit.epa_unit_id.label("unit_id"),
                Unit.type.label("unit_type"),
                Unit.primary_fuel.label("primary_fuel"),
                AnnualRecord.reporting_year.label("year"),
                column.label("value"),
            )
            .join(Facility, Facility.id == AnnualRecord.facility_id)
            .join(Unit, (Unit.internal_id == AnnualRecord.unit_key)
                  & (Unit.facility_id == AnnualRecord.facility_id))
            .where(*where, column.is_not(None))
        )
        tie_breaks = (Facility.name, Unit.epa_unit_id, AnnualRecord.reporting_year)
        order_inner = order_value

    # number the rows 1, 2, 3 ... (again from 1 inside each state when grouping by state)
    partition = Facility.state if a.get("group") == "state" else None
    numbered = base.add_columns(
        func.row_number()
        .over(partition_by=partition, order_by=(order_inner, *tie_breaks))
        .label("rank")
    ).subquery()
    top = select(numbered).where(numbered.c.rank <= a["n"])
    ordered = top.order_by(
        *((numbered.c.state,) if partition is not None else ()), numbered.c.rank
    )

    with Session() as session:
        # records that match the filters but have no value: shown to the user, never ranked
        no_value = select(func.count(AnnualRecord.id)).join(
            Facility, Facility.id == AnnualRecord.facility_id
        ).join(
            Unit, (Unit.internal_id == AnnualRecord.unit_key)
            & (Unit.facility_id == AnnualRecord.facility_id)
        ).where(*where, column.is_(None))
        out["excluded"] = session.scalar(no_value) or 0

        total = session.scalar(select(func.count()).select_from(top.subquery())) or 0
        pages = 1 if per_page is None else max(1, -(-total // per_page))
        page = min(max(page, 1), pages)
        query = ordered if per_page is None else ordered.limit(per_page).offset((page - 1) * per_page)
        out["rows"] = [dict(r) for r in session.execute(query).mappings()]
    out.update(total=total, page=page, pages=pages)
    return out
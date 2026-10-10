import csv
import io

import pandas as pd
from flask import Blueprint, Response, current_app, render_template, request, url_for
from sqlalchemy import func, select


from epadata.campd import CAMPDClient
from epadata.db import Session
from epadata.db.models import AnnualRecord, Dataset, Facility, Unit

bp = Blueprint("explore", __name__, url_prefix="/explore")


CSV_PATH = "epadata/data/sampledata.csv"
PER_PAGE = 25

# logical field -> column header in the CSV.  EDIT THE RIGHT-HAND SIDE to match your file.
COLS = {
    "facility_id": "Facility ID",
    "facility_name": "Facility Name",
    "unit_id": "Unit ID",
    "state": "State",
    "county": "County",
    "year": "Year",
    "primary_fuel": "Primary Fuel",
    "secondary_fuel": "Secondary Fuel",
    "unit_type": "Unit Type",
    "so2_control": "SO2 Control",
    "nox_control": "NOx Control",
    "pm_control": "PM Control",
    "op_hours": "Operating Hours",
    "gross_load": "Gross Load (MWh)",
    "heat_input": "Heat Input (MMBtu)",
    "co2": "CO2 (short tons)",
    "so2": "SO2 (short tons)",
    "nox": "NOx (short tons)",
}
EXACT = (
    "facility_id",
    "unit_id",
    "state",
)  # match whole value; commas allow several: 3,5,9
CONTAINS = (
    "facility_name",
    "county",
    "primary_fuel",
    "secondary_fuel",
    "unit_type",
    "so2_control",
    "nox_control",
    "pm_control",
)  # text "contains"
METRICS = (
    "op_hours",
    "gross_load",
    "heat_input",
    "co2",
    "so2",
    "nox",
)  # numeric: range + ranking

DATASET_PER_PAGE = 10
RECORD_PER_PAGE = 25
RECORD_SORTS = {
    "year": AnnualRecord.reporting_year,
    "facility": Facility.name,
    "state": Facility.state,
    "unit": Unit.epa_unit_id,
    "co2": AnnualRecord.co2_mass,
    "so2": AnnualRecord.so2_mass,
    "nox": AnnualRecord.nox_mass,
}


@bp.get("/")
def dataset_explorer():
    """Browse all datasets persisted in the application database via DataTables."""
    with Session() as session:
        query = select(Dataset).order_by(
            Dataset.retrieval_date.desc(), Dataset.id.desc()
        )
        datasets = session.scalars(query).all()

        dataset_rows = []
        for dataset in datasets:
            record_count = (
                session.scalar(
                    select(func.count(AnnualRecord.id)).where(
                        AnnualRecord.dataset_id == dataset.id
                    )
                )
                or 0
            )
            facility_count = (
                session.scalar(
                    select(func.count(func.distinct(AnnualRecord.facility_id))).where(
                        AnnualRecord.dataset_id == dataset.id
                    )
                )
                or 0
            )
            dataset_rows.append(
                {
                    "dataset": dataset,
                    "record_count": record_count,
                    "facility_count": facility_count,
                }
            )

    return render_template(
        "explore.html",
        dataset_rows=dataset_rows,
    )


@bp.get("/dataset/<int:dataset_id>")
def dataset_detail(dataset_id: int):
    """Show dataset detail view with a full Pandas DataFrame containing all facility, unit, and record attributes."""
    with Session() as session:
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            return render_template("explore.html", dataset_rows=[]), 404

        query = (
            select(
                Facility.id.label("Facility ID"),
                Facility.name.label("Facility Name"),
                Facility.state.label("State"),
                Facility.county.label("County"),
                Facility.latitude.label("Latitude"),
                Facility.longitude.label("Longitude"),
                Facility.source_category.label("Source Category"),
                Unit.epa_unit_id.label("Unit ID"),
                Unit.type.label("Unit Type"),
                Unit.primary_fuel.label("Primary Fuel"),
                Unit.secondary_fuel.label("Secondary Fuel"),
                Unit.operating_date.label("Operating Date"),
                Unit.retirement_date.label("Retirement Date"),
                AnnualRecord.reporting_year.label("Year"),
                AnnualRecord.operating_time.label("Operating Time (hrs)"),
                AnnualRecord.gross_load.label("Gross Load (MWh)"),
                AnnualRecord.steam_load.label("Steam Load (1000 lbs)"),
                AnnualRecord.heat_input.label("Heat Input (MMBtu)"),
                AnnualRecord.program_code.label("Program Code"),
                AnnualRecord.so2_controls.label("SO2 Controls"),
                AnnualRecord.nox_controls.label("NOx Controls"),
                AnnualRecord.pm_controls.label("PM Controls"),
                AnnualRecord.co2_mass.label("CO2 Mass (short tons)"),
                AnnualRecord.so2_mass.label("SO2 Mass (short tons)"),
                AnnualRecord.nox_mass.label("NOx Mass (short tons)"),
            )
            .join(Facility, Facility.id == AnnualRecord.facility_id)
            .join(
                Unit,
                (Unit.internal_id == AnnualRecord.unit_key)
                & (Unit.facility_id == AnnualRecord.facility_id),
            )
            .where(AnnualRecord.dataset_id == dataset_id)
        )

        df = pd.read_sql(query, session.connection())

    return render_template(
        "dataset_detail.html",
        dataset=dataset,
        data=df,
        total=len(df),
    )


@bp.get("/dataset/<int:dataset_id>/download")
def dataset_download(dataset_id: int):
    """Download every record from a saved dataset as a CSV containing all facility and unit metadata."""
    with Session() as session:
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            return "Dataset not found", 404

        query = (
            select(AnnualRecord, Facility, Unit)
            .join(Facility, Facility.id == AnnualRecord.facility_id)
            .join(
                Unit,
                (Unit.internal_id == AnnualRecord.unit_key)
                & (Unit.facility_id == AnnualRecord.facility_id),
            )
            .where(AnnualRecord.dataset_id == dataset_id)
            .order_by(AnnualRecord.reporting_year.asc())
        )
        records = session.execute(query).all()

        data_rows = []
        for annual, facility, unit in records:
            data_rows.append(
                {
                    "Facility ID": facility.id,
                    "Facility Name": facility.name,
                    "State": facility.state,
                    "County": facility.county,
                    "Latitude": facility.latitude,
                    "Longitude": facility.longitude,
                    "Source Category": facility.source_category,
                    "Unit ID": unit.epa_unit_id,
                    "Unit Type": unit.type,
                    "Primary Fuel": unit.primary_fuel,
                    "Secondary Fuel": unit.secondary_fuel,
                    "Operating Date": unit.operating_date,
                    "Retirement Date": unit.retirement_date,
                    "Year": annual.reporting_year,
                    "Operating Time (hrs)": annual.operating_time,
                    "Gross Load (MWh)": annual.gross_load,
                    "Steam Load (1000 lbs)": annual.steam_load,
                    "Heat Input (MMBtu)": annual.heat_input,
                    "Program Code": annual.program_code,
                    "SO2 Controls": annual.so2_controls,
                    "NOx Controls": annual.nox_controls,
                    "PM Controls": annual.pm_controls,
                    "CO2 Mass (short tons)": annual.co2_mass,
                    "SO2 Mass (short tons)": annual.so2_mass,
                    "NOx Mass (short tons)": annual.nox_mass,
                }
            )

        df = pd.DataFrame(data_rows)

    filename = f"dataset-{dataset_id}-{dataset.name}.csv".replace("/", "-")

    return Response(
        df.to_csv(index=False),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def load_data():
    df = pd.read_csv(CSV_PATH, dtype={COLS["facility_id"]: str, COLS["unit_id"]: str})
    for key in METRICS + ("year",):
        if COLS[key] in df.columns:
            df[COLS[key]] = pd.to_numeric(df[COLS[key]], errors="coerce")
    return df


def parse_years(text):
    """'2015, 2019-2022' -> [2015, 2019, 2020, 2021, 2022]"""
    years = set()
    for part in text.replace(" ", "").split(","):
        a, _, b = part.partition("-")
        if a.isdigit() and b.isdigit():
            years.update(range(int(a), int(b) + 1))
        elif a.isdigit() and not b:
            years.add(int(a))
    return sorted(years)


def apply_filters(df, args):
    for key in EXACT:
        col, raw = COLS[key], args.get(key, "").strip()
        if raw and col in df.columns:
            wanted = [v.strip().lower() for v in raw.split(",") if v.strip()]
            df = df[df[col].astype(str).str.lower().isin(wanted)]

    for key in CONTAINS:
        col, raw = COLS[key], args.get(key, "").strip()
        if raw and col in df.columns:
            df = df[df[col].astype(str).str.contains(raw, case=False, regex=False)]

    years = parse_years(args.get("years", ""))
    if years and COLS["year"] in df.columns:
        df = df[df[COLS["year"]].isin(years)]

    for key in METRICS:
        col = COLS[key]
        if col not in df.columns:
            continue
        low, high = (
            args.get(key + "_min", type=float),
            args.get(key + "_max", type=float),
        )
        if low is not None:
            df = df[df[col] >= low]
        if high is not None:
            df = df[df[col] <= high]
    return df


def apply_ranking(df, args):
    key = args.get("rank_by", "")
    if key not in METRICS or COLS[key] not in df.columns:
        return df
    col = COLS[key]

    if args.get("level") == "facility":  # add a facility's units together first
        ids = [
            COLS[k]
            for k in ("facility_id", "facility_name", "state", "year")
            if COLS[k] in df.columns
        ]
        sums = [COLS[k] for k in METRICS if COLS[k] in df.columns]
        df = df.groupby(ids, as_index=False)[sums].sum(min_count=1)

    df = df.dropna(subset=[col]).sort_values(
        col, ascending=args.get("rank_dir") == "asc"
    )
    n = args.get("top", type=int)
    if n and n > 0:
        if args.get("group") == "state" and COLS["state"] in df.columns:
            df = df.groupby(COLS["state"], sort=False).head(
                n
            )  # top N inside each state
        else:
            df = df.head(n)
    return df


def search_dataframe(args):
    df = apply_ranking(apply_filters(load_data(), args), args)
    sort = args.get("sort")
    if sort in df.columns:  # only real column names are accepted
        df = df.sort_values(
            sort, ascending=args.get("dir") != "desc", na_position="last"
        )
    return df


def build_results(args):
    df = search_dataframe(args)
    total = len(df)
    pages = max(1, -(-total // PER_PAGE))
    page = min(max(args.get("page", 1, type=int), 1), pages)
    chunk = df.iloc[(page - 1) * PER_PAGE : page * PER_PAGE].fillna("")

    # Link each row to a unit page, but only once a details.unit route exists.
    fid, uid = COLS["facility_id"], COLS["unit_id"]

    has_links = (
        "details.unit" in current_app.view_functions  # app.view_functions line??#
        and fid in chunk.columns
        and uid in chunk.columns
    )
    rows = []
    for rec in chunk.to_dict("records"):
        link = (
            url_for("details.unit", facility_id=rec[fid], unit_id=rec[uid])
            if has_links
            else None
        )  # url_for is not imported?
        rows.append((link, list(rec.values())))

    return dict(
        columns=list(chunk.columns),
        rows=rows,
        has_links=has_links,
        total=total,
        page=page,
        pages=pages,
        sort=args.get("sort", ""),
        direction=args.get("dir", "asc"),
        args_no_page={k: v for k, v in args.items() if v and k != "page"},
        args_no_sort={
            k: v for k, v in args.items() if v and k not in ("page", "sort", "dir")
        },
    )


def wants_fragment():
    h = request.headers
    return (
        h.get("HX-Request") == "true" and h.get("HX-History-Restore-Request") != "true"
    )


@bp.after_request
def vary_on_htmx(
    resp,
):  # same URL returns a fragment or a full page, so caches must know
    resp.vary.add("HX-Request")
    return resp


@bp.route("/campd")
def campd():
    results = build_results(request.args) if any(request.args.values()) else {}

    if wants_fragment():  # htmx asked: send only the results, no client/API calls
        if not results:
            return '<p class="text-muted">Enter at least one search criterion.</p>'
        return render_template("fragments/search/results.html", **results)

    client = CAMPDClient()
    return render_template(
        "explore.html",
        state_codes=client.get_state_codes(),
        fuel_type_codes=client.get_fuel_type_codes(),
        **results,
    )


@bp.get("/campd/download")
def campd_download():
    df = search_dataframe(request.args)  # every matching row, not just one page
    return Response(
        df.to_csv(index=False),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=epadata_search.csv"},
    )

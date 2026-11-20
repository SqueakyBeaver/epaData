# NEW: tests for "one dataset per state, no empty datasets" (importer.py).
from sqlalchemy import select

from epadata.db.models import AnnualRecord, Dataset
from epadata.importer import import_annual_data


def row(year, unit="1", co2=100.0, facility=1, state="FL"):
    return {"facilityId": facility, "facilityName": "Plant", "stateCode": state, "unitId": unit,
            "year": year, "unitType": "Boiler", "primaryFuelInfo": "Coal",
            "sumOpTime": 5000, "grossLoad": 1000, "co2Mass": co2, "so2Mass": 1, "noxMass": 1}


def run(session, rows, **kw):
    return import_annual_data(session, rows, [], name="EPA CAMPD FL (bulk load)",
                              years=sorted({r["year"] for r in rows}), reuse_dataset=True, **kw)


def datasets(session):
    return session.scalars(select(Dataset)).all()


def test_first_run_creates_one_dataset(session):
    r = run(session, [row(2020), row(2021)])
    assert r["accepted"] == 2
    [d] = datasets(session)
    assert d.name == "EPA CAMPD FL (bulk load)" and d.reporting_years == [2020, 2021]


def test_second_run_adds_to_the_same_dataset(session):
    run(session, [row(2020)])
    r = run(session, [row(2021)])
    assert r["accepted"] == 1
    [d] = datasets(session)
    assert d.reporting_years == [2020, 2021]
    assert d.accepted_records_cnt == 2 and d.raw_records_cnt == 2
    assert d.query_params["years"] == [2020, 2021]
    assert "Added" in d.notes


def test_a_run_with_nothing_new_creates_no_dataset(session):
    r = run(session, [row(2020)])
    again = run(session, [row(2020)])
    assert again["accepted"] == 0 and again["skipped_identical"] == 1
    assert len(datasets(session)) == 1                      # the original, unchanged
    assert datasets(session)[0].accepted_records_cnt == 1


def test_nothing_new_on_a_fresh_database_leaves_no_empty_dataset(session):
    # first save the data under another dataset, then pull the same data as a new bulk dataset
    import_annual_data(session, [row(2020)], [], name="manual pull", years=[2020])
    r = run(session, [row(2020)])
    assert r["accepted"] == 0 and r["dataset_id"] is None
    assert [d.name for d in datasets(session)] == ["manual pull"]


def test_changed_values_are_kept_as_a_new_version_in_a_revised_dataset(session):
    run(session, [row(2020, co2=100.0)])
    r = run(session, [row(2020, co2=150.0)])
    assert r["new_versions"] == 1
    main, revised = sorted(datasets(session), key=lambda d: d.id)
    assert main.accepted_records_cnt == 1 and main.name == "EPA CAMPD FL (bulk load)"
    assert revised.name.startswith("EPA CAMPD FL (bulk load) revised ") and revised.accepted_records_cnt == 1
    assert r["revised_dataset_name"] == revised.name
    assert len(session.scalars(select(AnnualRecord)).all()) == 2   # nothing overwritten


def test_normal_retrieval_still_gets_a_new_dataset_each_time(session):
    a = import_annual_data(session, [row(2020)], [], name="pull", years=[2020])
    b = import_annual_data(session, [row(2021)], [], name="pull", years=[2021])
    assert a["dataset_name"] == "pull" and b["dataset_name"] == "pull (2)"
import pandas as pd

from epadata import dataset_builder


def table(s, **kw):
    return dataset_builder.build_analysis_table(s, dataset_builder.Filters(**kw))


def test_a_unit_year_in_two_datasets_is_counted_once_and_the_newest_wins(small_db):
    df = table(small_db)
    u1_2021 = df[(df.facility_id == 1) & (df.unit_id == "1") & (df.year == 2021)]
    assert len(u1_2021) == 1
    assert u1_2021.iloc[0].co2_mass == 1200 and u1_2021.iloc[0].dataset_id == 2
    assert len(df) == 6


def test_choosing_one_dataset_uses_only_that_dataset(small_db):
    df = table(small_db, dataset_id=1)
    assert len(df) == 6 and set(df.dataset_id) == {1}
    assert df[(df.unit_id == "1") & (df.year == 2021)].iloc[0].co2_mass == 1000


def test_filters(small_db):
    assert set(table(small_db, states=["OH"]).state) == {"OH"}
    assert set(table(small_db, fuels=["coal"]).primary_fuel) == {"Coal"}              # not case sensitive
    assert set(table(small_db, unit_types=["turbine"]).unit_id) == {"CT1"}
    assert set(table(small_db, controls=["limestone"]).facility_id) == {1}            # looked for in SO2 controls
    assert set(table(small_db, controls=["low nox"]).facility_id) == {2}              # ... and NOx controls
    assert set(table(small_db, programs=["arp"]).facility_id) == {1}
    assert set(table(small_db, facility_ids=[2]).facility_id) == {2}
    assert set(table(small_db, unit_ids=["ct1"]).unit_id) == {"CT1"}
    assert set(table(small_db, year_from=2021).year) == {2021}


def test_minimums_drop_records_whose_value_is_missing(small_db):
    df = table(small_db, min_gross_load=1)
    assert df.gross_load.notna().all() and (df.gross_load >= 1).all()   # the missing-load record is gone
    assert len(table(small_db, min_op_hours=4000)) == 3                 # u1: 2019, 2020 and 2021 (newest copy)


def test_summary_counts_and_missing(small_db):
    s = dataset_builder.summarize(table(small_db))
    assert s["counts"] == {"records": 6, "facilities": 2, "units": 3, "years": [2019, 2020, 2021]}
    missing = {m["variable"]: m for m in s["missing"]}
    assert missing["so2_mass"]["missing"] == 2          # u2 2021 and u3 2020
    assert missing["co2_intensity"]["missing"] == 2     # zero load and missing load


def test_empty_result_is_handled(small_db):
    df = table(small_db, states=["ZZ"])
    s = dataset_builder.summarize(df)
    assert s["counts"]["records"] == 0 and s["counts"]["years"] == []
    assert all(c in df.columns for c in dataset_builder.EXPORT_COLUMNS)


def test_filters_from_url_and_back():
    f = dataset_builder.Filters.from_args({"year_from": "2000", "year_to": "2005", "state": "ky, oh",
                                    "facility_id": "1,x,3", "fuel": "Coal", "min_gross_load": "abc"})
    assert f.states == ["KY", "OH"] and f.facility_ids == [1, 3]
    assert len(f.errors) == 2                                # 'x' and 'abc' reported, not silently dropped
    url_args = {k: str(v) for k, v in f.to_query().items()}   # in a real URL every value is text
    assert dataset_builder.Filters.from_args(url_args).to_query() == f.to_query()   # round trip = reproducible


def test_export_metadata_records_filters_and_definitions(small_db):
    f = dataset_builder.Filters(states=["KY"])
    df = dataset_builder.build_analysis_table(small_db, f)
    meta = dataset_builder.export_metadata(small_db, f, df)
    assert meta["Filter: States"] == "KY"
    assert "Definition: co2_intensity" in meta and "Software / pipeline version" in meta
    assert "1: old download" in meta["Dataset identifiers"]
import numpy as np
import pandas as pd
import pytest

from epadata import dataset_builder


def test_safe_divide_never_returns_zero_or_infinity_for_undefined_results():
    num = pd.Series([10.0, 10.0, 10.0, np.nan, 0.0])
    den = pd.Series([2.0, 0.0, -5.0, 2.0, 4.0])
    result = dataset_builder.safe_divide(num, den)
    assert result[0] == 5.0
    assert result[1:4].isna().all()      # zero, negative, missing numerator -> missing
    assert result[4] == 0.0              # a real 0 / 4 is a real zero


def test_hours_in_year():
    assert dataset_builder.hours_in_year([2019, 2020, 2100, 2000]).tolist() == [8760, 8784, 8760, 8784]


def test_intensities_heat_rate_and_utilization(small_db):
    df = dataset_builder.build_analysis_table(small_db, dataset_builder.Filters(dataset_id=1))
    row = df[(df.facility_id == 1) & (df.unit_id == "1") & (df.year == 2019)].iloc[0]
    assert row.co2_intensity == pytest.approx(1.1)        # 1100 / 1000
    assert row.so2_intensity == pytest.approx(0.01)
    assert row.nox_intensity == pytest.approx(0.005)
    assert row.heat_rate == pytest.approx(10.0)           # 10000 / 1000
    assert row.operating_utilization == pytest.approx(8000 / 8760)
    leap = df[(df.facility_id == 1) & (df.unit_id == "1") & (df.year == 2020)].iloc[0]
    assert leap.operating_utilization == pytest.approx(4380 / 8784)


def test_zero_or_missing_gross_load_leaves_intensity_missing(small_db):
    df = dataset_builder.build_analysis_table(small_db, dataset_builder.Filters(dataset_id=1))
    zero_load = df[(df.unit_id == "2") & (df.year == 2020)].iloc[0]
    assert pd.isna(zero_load.co2_intensity) and pd.isna(zero_load.heat_rate)
    no_load = df[(df.unit_id == "2") & (df.year == 2021)].iloc[0]
    assert pd.isna(no_load.co2_intensity) and pd.isna(no_load.gross_load)


def test_year_over_year(small_db):
    df = dataset_builder.build_analysis_table(small_db, dataset_builder.Filters(dataset_id=1))
    u1 = df[(df.facility_id == 1) & (df.unit_id == "1")].set_index("year")
    assert pd.isna(u1.loc[2019, "co2_mass_yoy_abs"])                      # no 2018 in the data
    assert u1.loc[2020, "co2_mass_yoy_abs"] == pytest.approx(-550)        # 550 - 1100
    assert u1.loc[2020, "co2_mass_yoy_pct"] == pytest.approx(-50.0)
    assert u1.loc[2021, "co2_mass_yoy_pct"] == pytest.approx(81.818, abs=1e-3)


def test_year_over_year_is_missing_when_previous_year_is_zero_or_absent(small_db):
    df = dataset_builder.build_analysis_table(small_db, dataset_builder.Filters(dataset_id=1))
    u2 = df[df.unit_id == "2"].set_index("year")
    assert pd.isna(u2.loc[2020, "co2_mass_yoy_abs"])        # unit 2 has no 2019 record
    assert u2.loc[2021, "co2_mass_yoy_abs"] == pytest.approx(50)  # 50 - 0 is a real change
    assert pd.isna(u2.loc[2021, "co2_mass_yoy_pct"])        # but a % of zero is undefined


def test_year_filter_still_sees_the_year_before_for_yoy(small_db):
    df = dataset_builder.build_analysis_table(small_db, dataset_builder.Filters(year_from=2020, year_to=2020, dataset_id=1))
    assert set(df.year) == {2020}                           # 2019 was loaded but removed again
    u1 = df[(df.facility_id == 1) & (df.unit_id == "1")].iloc[0]
    assert u1.co2_mass_yoy_abs == pytest.approx(-550)
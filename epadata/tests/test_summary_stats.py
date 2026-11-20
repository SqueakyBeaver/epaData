import numpy as np
import pandas as pd
import pytest

from epadata import dataset_builder, summary_stats, traci
from test_traci import GOOD


def table(session, **filters):
    return dataset_builder.build_analysis_table(session, dataset_builder.Filters(**filters))


def test_describe_matches_hand_calculation():
    d = summary_stats.describe(pd.Series([1, 2, 3, 4, 100, np.nan]))
    assert d["Count (not missing)"] == 5 and d["Missing"] == 1
    assert d["Minimum"] == 1 and d["Maximum"] == 100
    assert d["Mean"] == pytest.approx(22.0) and d["Median"] == 3
    assert d["Standard deviation"] == pytest.approx(np.std([1, 2, 3, 4, 100], ddof=1))   # sample std
    assert d["First quartile (Q1)"] == 2 and d["Third quartile (Q3)"] == 4
    assert d["Interquartile range (Q3 - Q1)"] == 2
    assert "95th percentile" in d


def test_describe_handles_empty_and_single_values():
    d = summary_stats.describe(pd.Series([np.nan, np.nan]))
    assert d["Count (not missing)"] == 0 and np.isnan(d["Mean"])
    one = summary_stats.describe(pd.Series([5.0]))
    assert one["Mean"] == 5 and np.isnan(one["Standard deviation"])      # needs 2+ values


def test_grouped_stats_by_state_and_year(small_db):
    df = table(small_db)
    by_state = summary_stats.grouped_stats(df, "state", "co2_mass")
    assert by_state.loc["KY", "total"] == pytest.approx(1100 + 550 + 1200 + 0 + 50)
    assert by_state.loc["OH", "total"] == pytest.approx(117)
    assert by_state.loc["KY", "records"] == 5
    by_year = summary_stats.grouped_stats(df, "year", "so2_mass")
    assert by_year.loc[2020, "missing"] == 1            # the gas turbine reported no SO2
    assert by_year.loc[2020, "total"] == pytest.approx(4 + 0)
    assert list(by_year.index) == [2019, 2020, 2021]    # years are in order


def test_all_missing_group_has_missing_total_not_zero(small_db):
    df = table(small_db, unit_ids=["CT1"])
    by = summary_stats.grouped_stats(df, "state", "so2_mass")
    assert np.isnan(by.loc["OH", "total"]) and by.loc["OH", "count"] == 0


def test_total_is_only_given_for_variables_where_it_makes_sense(small_db):
    df = table(small_db)
    assert "total" in summary_stats.grouped_stats(df, "state", "co2_mass")
    assert "total" not in summary_stats.grouped_stats(df, "state", "co2_intensity")


def test_rank_top_and_bottom(small_db):
    df = table(small_db)
    top = summary_stats.rank(df, "unit", "co2_mass", "sum", 2, "top")
    assert top["rank"].tolist() == [1, 2]
    assert top.iloc[0]["value"] >= top.iloc[1]["value"]
    bottom = summary_stats.rank(df, "state", "co2_mass", "sum", 1, "bottom")
    assert bottom.iloc[0]["State"] == "OH"
    # a total of an intensity is meaningless -> falls back to the average
    assert summary_stats.rank(df, "state", "co2_intensity", "sum", 5, "top").attrs["aggregate"] == "mean"


def test_rank_leaves_out_groups_with_no_values(small_db):
    df = table(small_db, unit_ids=["CT1"])
    assert summary_stats.rank(df, "state", "so2_mass", "sum", 5, "top").empty


def make_frame(a_values, b_values, var="co2_mass"):
    rows = [{"state": "AA", "year": 2020, "unit_key": i, "facility_id": 1} for i, _ in enumerate(a_values)]
    rows += [{"state": "BB", "year": 2020, "unit_key": 100 + i, "facility_id": 2} for i, _ in enumerate(b_values)]
    df = pd.DataFrame(rows)
    for name in summary_stats.COMPARE_VARIABLES:
        df[name] = np.nan
    df[var] = list(a_values) + list(b_values)
    for c in ("so2_controls", "nox_controls", "pm_controls", "primary_fuel", "unit_type"):
        df[c] = None
    return df


def test_split_groups_rules(small_db):
    df = table(small_db)
    a, b, la, lb, err = summary_stats.split_groups(df, "state", "KY", "OH")
    assert err is None and set(a.state) == {"KY"} and set(b.state) == {"OH"}
    *_, err = summary_stats.split_groups(df, "state", "KY", "KY")
    assert "both groups" in err                                         # overlapping groups are refused
    *_, err = summary_stats.split_groups(df, "state", "KY", "ZZ")
    assert "Group B has no records" in err
    *_, err = summary_stats.split_groups(df, "year", "2019", "oops")
    assert "Years must look like" in err
    a, b, *_ , err = summary_stats.split_groups(df, "year", "2019-2020", "2021")
    assert err is None and set(a.year) <= {2019, 2020} and set(b.year) == {2021}
    a, b, la, lb, err = summary_stats.split_groups(df, "control", "limestone", "")
    assert err is None and set(a.facility_id) == {1} and (~b.so2_controls.fillna("").str.contains("Limestone")).all()
    assert la == "with 'limestone'" and lb == "without 'limestone'"


def test_compare_groups_known_answer():
    a = [10.0, 12.0, 11.0, 13.0, 9.0, 10.5, 11.5, 12.5]
    b = [20.0, 22.0, 21.0, 23.0, 19.0, 20.5, 21.5, 22.5]
    df = make_frame(a, b)
    r = summary_stats.compare_groups(df[df.state == "AA"], df[df.state == "BB"])
    row = r[r.Variable == "CO2 mass"].iloc[0]
    assert row["A: mean"] == pytest.approx(np.mean(a)) and row["B: mean"] == pytest.approx(np.mean(b))
    assert row["Difference in means (B - A)"] == pytest.approx(10.0)
    assert row["% difference (relative to A's mean)"] == pytest.approx(10.0 / np.mean(a) * 100)
    assert row["Difference in medians (B - A)"] == pytest.approx(np.median(b) - np.median(a))
    from scipy import stats
    assert row["p-value (Welch t-test)"] == pytest.approx(stats.ttest_ind(b, a, equal_var=False).pvalue)
    assert row["p-value (Mann-Whitney)"] == pytest.approx(stats.mannwhitneyu(a, b).pvalue)
    assert row["95% CI of difference: low"] < 10.0 < row["95% CI of difference: high"]   # CI contains the true difference
    assert row["Detectable difference?"] == "yes"


def test_compare_says_so_when_there_is_no_difference_or_too_little_data():
    same = make_frame([10, 11, 12, 13], [10.5, 11.5, 11.0, 12.5])
    row = summary_stats.compare_groups(same[same.state == "AA"], same[same.state == "BB"]).query("Variable == 'CO2 mass'").iloc[0]
    assert row["Detectable difference?"] == "no"
    tiny = make_frame([10], [20])
    row = summary_stats.compare_groups(tiny[tiny.state == "AA"], tiny[tiny.state == "BB"]).query("Variable == 'CO2 mass'").iloc[0]
    assert row["Detectable difference?"] == "not enough data" and np.isnan(row["p-value (Welch t-test)"])
    # a variable with no values at all is reported as missing, not zero
    other = summary_stats.compare_groups(tiny[tiny.state == "AA"], tiny[tiny.state == "BB"]).query("Variable == 'SO2 mass'").iloc[0]
    assert other["A: values"] == 0 and np.isnan(other["A: mean"])


def test_impact_by_group(small_db):
    small_db_session = small_db
    traci.import_factors(small_db_session, GOOD)
    df = table(small_db_session, dataset_id=1)
    long, info = summary_stats.impact_by_group(df, traci.load_factors(small_db_session), "state")
    kg = 907.18474
    ky = long[(long.State == "KY") & (long.impact_category == "Global warming")].iloc[0]
    # KY unit-years with a CO2 value in dataset 1: 1100, 550, 1000, 0, 50 short tons
    assert ky["total_impact"] == pytest.approx((1100 + 550 + 1000 + 0 + 50) * kg)
    assert ky["unit_years_included"] == 5 and ky["unit_years_missing"] == 0
    # per MWh uses only unit-years with a result AND a positive load: 1000 + 500 + 1000 MWh
    assert ky["impact_per_mwh"] == pytest.approx((1100 + 550 + 1000) * kg / (1000 + 500 + 1000))
    oh = long[(long.State == "OH") & (long.impact_category == "Acidification")].iloc[0]
    assert oh["unit_years_included"] == 0 and oh["unit_years_missing"] == 1      # SO2 missing -> no result
    assert np.isnan(oh["total_impact"])                                          # missing, not 0


def test_impact_by_group_without_factors_is_empty(small_db):
    long, info = summary_stats.impact_by_group(table(small_db), traci.load_factors(small_db), "state")
    assert long.empty and info == {}
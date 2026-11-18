import numpy as np
import pandas as pd
import pytest

from epadata import traci

# Numbers below are INVENTED to test the arithmetic only. They are not real TRACI factors.
GOOD = """traci_version,campd_pollutant,substance,cas,medium,impact_category,factor,factor_unit,verified
T1,CO2,Carbon dioxide,124-38-9,air,Global warming,1,kg CO2 eq / kg,yes
T1,SO2,Sulfur dioxide,7446-09-5,air,Acidification,1,kg SO2 eq / kg,yes
T1,NOX,Nitrogen dioxide,10102-44-0,air,Acidification,0.5,kg SO2 eq / kg,yes
T1,NOX,Nitric oxide,10102-43-9,air,Acidification,0.4,kg SO2 eq / kg,no
"""


def test_short_tons_to_kg():
    assert traci.short_tons_to_kg(1) == pytest.approx(907.18474)
    assert traci.short_tons_to_kg(1000) == pytest.approx(907184.74)


def test_import_only_verified_rows(session):
    r = traci.import_factors(session, GOOD)
    assert r["ok"] and r["imported"] == 3 and r["skipped"] == 1 and r["version"] == "T1"
    assert len(traci.load_factors(session)) == 3


def test_import_is_repeatable_and_replaces_the_version(session):
    traci.import_factors(session, GOOD)
    traci.import_factors(session, GOOD)
    assert len(traci.load_factors(session)) == 3


def test_two_verified_factors_for_one_pollutant_and_category_is_refused(session):
    bad = GOOD.replace("Nitric oxide,10102-43-9,air,Acidification,0.4,kg SO2 eq / kg,no",
                       "Nitric oxide,10102-43-9,air,Acidification,0.4,kg SO2 eq / kg,yes")
    r = traci.import_factors(session, bad)
    assert not r["ok"] and "already has a verified factor" in r["errors"][0]
    assert len(traci.load_factors(session)) == 0         # nothing half-imported


@pytest.mark.parametrize("old,new,words", [
    (",air,Global warming", ",water,Global warming", "only air"),
    ("T1,CO2,", "T1,CO3,", "CO2, SO2 or NOX"),
    (",1,kg CO2 eq / kg,yes", ",one,kg CO2 eq / kg,yes", "not a number"),
])
def test_import_rejects_bad_rows(session, old, new, words):
    r = traci.import_factors(session, GOOD.replace(old, new, 1))
    assert not r["ok"] and words in " ".join(r["errors"])


def test_import_rejects_missing_columns_and_empty(session):
    assert not traci.import_factors(session, "a,b\n1,2\n")["ok"]
    nothing = "traci_version,campd_pollutant,substance,cas,medium,impact_category,factor,factor_unit,verified\n"
    assert "No rows have verified" in traci.import_factors(session, nothing)["errors"][0]


def test_calculate_impacts(session):
    traci.import_factors(session, GOOD)
    df = pd.DataFrame({
        "co2_mass": [1000.0, np.nan, 10.0, 0.0],
        "so2_mass": [10.0, 10.0, np.nan, 0.0],
        "nox_mass": [4.0, 4.0, 4.0, 0.0],
        "gross_load": [2000.0, 100.0, 0.0, np.nan],
    })
    out, info = traci.calculate_impacts(df, traci.load_factors(session))
    kg = 907.18474
    # Global warming uses CO2 only: 1000 short tons -> kg x 1
    assert out.loc[0, "impact_global_warming"] == pytest.approx(1000 * kg * 1)
    # Acidification adds SO2 and NOx WITHIN the category: (10 x 1 + 4 x 0.5) short tons-worth
    assert out.loc[0, "impact_acidification"] == pytest.approx((10 * 1 + 4 * 0.5) * kg)
    # per MWh
    assert out.loc[0, "impact_acidification_per_mwh"] == pytest.approx((10 + 2) * kg / 2000)
    # a missing CO2 mass leaves the global-warming impact missing (NOT zero) ...
    assert np.isnan(out.loc[1, "impact_global_warming"])
    # ... but acidification does not need CO2, so it is still calculated
    assert out.loc[1, "impact_acidification"] == pytest.approx(12 * kg)
    # missing SO2 -> acidification missing
    assert np.isnan(out.loc[2, "impact_acidification"])
    # zero or missing gross load -> intensity missing; true zero emissions -> true zero impact
    assert np.isnan(out.loc[2, "impact_global_warming_per_mwh"]) and np.isnan(out.loc[3, "impact_global_warming_per_mwh"])
    assert out.loc[3, "impact_global_warming"] == 0
    assert info["Acidification"]["unit"] == "kg SO2 eq" and info["Acidification"]["traci_version"] == "T1"
    assert set(info["Acidification"]["factors"]) == {"SO2", "NOX"}


def test_categories_stay_separate():
    assert traci.category_slug("Human health - particulate") == "human_health_particulate"


def test_the_included_factor_file_matches_epas_traci_2_2_spreadsheet(session):
    """The shipped epadata/data/traci_factors.csv. These numbers were read from EPA's traci_2_2.xlsx
    (sheet "Substances"): CO2 -> Global Climate 1; SO2 -> Acidification 1 and HH Particulate 0.0611...;
    the "NITROGEN OXIDES" row -> Acidification 0.7, HH Particulate 0.00722..., Photochemical 24.79..."""
    from pathlib import Path
    text = (Path(traci.__file__).parent / "data" / "traci_factors.csv").read_text(encoding="utf-8")
    result = traci.import_factors(session, text)
    assert result["ok"] and result["version"] == "2.2" and result["imported"] == 6
    f = traci.load_factors(session).set_index(["campd_pollutant", "impact_category"])["factor"]
    assert f[("CO2", "Global Climate")] == 1
    assert f[("SO2", "Acidification")] == 1
    assert f[("SO2", "HH Particulate")] == pytest.approx(0.0611111, abs=1e-6)
    assert f[("NOX", "Acidification")] == 0.7
    assert f[("NOX", "HH Particulate")] == pytest.approx(0.0072222, abs=1e-6)
    assert f[("NOX", "Photochemical Oxidation Formation")] == pytest.approx(24.7935897, abs=1e-6)
    assert ("CO2", "Acidification") not in f.index      # CO2 has no factor there, so it is not invented
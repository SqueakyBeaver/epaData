# NEW: tests for the drop-down filters (state names, several choices) and the full-text search
# box that moved from the EPA Retrieval page to the Dataset Explorer page.
from werkzeug.datastructures import MultiDict

from epadata import dataset_builder as b
from epadata.db.models import AnnualRecord, Dataset, Facility, Unit


def test_state_names_and_codes_all_work():
    for typed in ("FL", "fl", "Florida", "florida", " FLORIDA "):
        assert b.Filters.from_args({"state": typed}).states == ["FL"]
    assert b.Filters.from_args({"state": "New York, ohio"}).states == ["NY", "OH"]


def test_dropdowns_send_one_parameter_per_choice():
    args = MultiDict([("state", "FL"), ("state", "GA"), ("fuel", "Coal"), ("fuel", "Pipeline Natural Gas")])
    f = b.Filters.from_args(args)
    assert f.states == ["FL", "GA"] and f.fuels == ["Coal", "Pipeline Natural Gas"]


def test_filters_still_round_trip_through_the_url():
    f = b.Filters.from_args(MultiDict([("state", "FL"), ("state", "GA"), ("control", "Scrubber")]))
    again = b.Filters.from_args({k: str(v) for k, v in f.to_query().items()})
    assert again.states == ["FL", "GA"] and again.controls == ["Scrubber"]


def test_filter_options_list_what_is_in_the_database(small_db):
    b._options_cache["value"] = None
    s = small_db
    o = b.filter_options(s)
    assert ("KY", "Kentucky (KY)") in o["states"] and ("OH", "Ohio (OH)") in o["states"]
    assert o["fuels"] and o["unit_types"]
    b._options_cache["value"] = None


def test_options_split_combined_entries(session):
    b._options_cache["value"] = None
    d = Dataset(name="d", data_source="x", reporting_years=[2020]); session.add(d)
    session.add(Facility(id=1, name="P", state="FL")); session.flush()
    u = Unit(facility_id=1, epa_unit_id="1", type="Boiler", primary_fuel="Coal"); session.add(u); session.flush()
    session.add(AnnualRecord(dataset_id=d.id, facility_id=1, unit_key=u.internal_id, reporting_year=2020,
                             program_code="ARP, CSNOX", so2_controls="Wet Limestone"))
    session.flush()
    o = b.filter_options(session)
    assert o["programs"] == ["ARP", "CSNOX"] and o["controls"] == ["Wet Limestone"]
    b._options_cache["value"] = None
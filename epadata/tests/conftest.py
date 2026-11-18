# NEW: shared test setup. Tests use their OWN empty in-memory database, never your real db.sqlite.
import os
import tempfile

# epadata.db creates "db.sqlite" in the current folder when it is first imported. Do that inside a
# throw-away folder so running the tests can never touch (or create) a file in your project.
_HERE = os.getcwd()
os.chdir(tempfile.mkdtemp())
os.environ.setdefault("CAMPD_API_KEY", "test-key-not-real")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from epadata.db.models import AnnualRecord, Base, Dataset, Facility, Unit

os.chdir(_HERE)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as s:
        yield s


def add_record(s, dataset, facility, unit, year, **values):
    s.add(AnnualRecord(dataset_id=dataset.id, facility_id=facility.id, unit_key=unit.internal_id,
                       reporting_year=year, **values))


@pytest.fixture()
def small_db(session):
    """2 facilities (KY coal, OH gas), 3 units, a few years, with deliberate gaps and zeros."""
    s = session
    d1 = Dataset(name="old download", data_source="EPA CAMPD API", reporting_years=[2019, 2020, 2021])
    d2 = Dataset(name="newer download", data_source="EPA CAMPD API", reporting_years=[2021])
    s.add_all([d1, d2]); s.flush()
    f1 = Facility(id=1, name="Coal Plant", state="KY"); f2 = Facility(id=2, name="Gas Plant", state="OH")
    s.add_all([f1, f2]); s.flush()
    u1 = Unit(facility_id=1, epa_unit_id="1", type="Dry bottom wall-fired boiler", primary_fuel="Coal")
    u2 = Unit(facility_id=1, epa_unit_id="2", type="Dry bottom wall-fired boiler", primary_fuel="Coal")
    u3 = Unit(facility_id=2, epa_unit_id="CT1", type="Combustion turbine", primary_fuel="Pipeline Natural Gas")
    s.add_all([u1, u2, u3]); s.flush()
    # u1: complete 2019-2021
    add_record(s, d1, f1, u1, 2019, operating_time=8000, gross_load=1000, heat_input=10000, co2_mass=1100, so2_mass=10, nox_mass=5, so2_controls="Wet Limestone", program_code="ARP")
    add_record(s, d1, f1, u1, 2020, operating_time=4380, gross_load=500, heat_input=5500, co2_mass=550, so2_mass=4, nox_mass=2, so2_controls="Wet Limestone", program_code="ARP")
    add_record(s, d1, f1, u1, 2021, operating_time=8000, gross_load=1000, heat_input=10000, co2_mass=1000, so2_mass=9, nox_mass=4, so2_controls="Wet Limestone", program_code="ARP")
    # the SAME unit-year 2021 downloaded again later with corrected numbers -> must not be counted twice
    add_record(s, d2, f1, u1, 2021, operating_time=8000, gross_load=1000, heat_input=10000, co2_mass=1200, so2_mass=9, nox_mass=4, so2_controls="Wet Limestone", program_code="ARP")
    # u2: gross load ZERO in 2020 (intensity undefined) and a missing year 2019 -> 2020 has no previous year
    add_record(s, d1, f1, u2, 2020, operating_time=0, gross_load=0, heat_input=0, co2_mass=0, so2_mass=0, nox_mass=0)
    add_record(s, d1, f1, u2, 2021, operating_time=100, gross_load=None, heat_input=None, co2_mass=50, so2_mass=None, nox_mass=1)
    # u3: gas turbine, 2020 only, no SO2 reported
    add_record(s, d1, f2, u3, 2020, operating_time=3000, gross_load=200, heat_input=2000, co2_mass=117, so2_mass=None, nox_mass=0.5, nox_controls="Low NOx Burners")
    s.commit()
    return s
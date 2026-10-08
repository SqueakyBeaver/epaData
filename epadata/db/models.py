from datetime import date, datetime
# this is creating the db, basically the model for it # 
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Engine,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    registry,
    relationship,
)


# SQLite ignores foreign keys unless every new connection turns them on.
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(MappedAsDataclass, DeclarativeBase, kw_only=True):
    registry = registry(
        type_annotation_map={
            list[int]: JSON,
            list[str]: JSON,
        }
    )


class Dataset(Base):
    """One row per import: an EPA retrieval or a user upload."""

    __tablename__ = "dataset"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    data_source: Mapped[str]  # "EPA CAMPD API" or "User upload"
    reporting_years: Mapped[list[int]]  # e.g. [2020, 2021, 2022]
    retrieval_date: Mapped[datetime] = mapped_column(
        init=False, server_default=func.now()
    )
    original_filename: Mapped[str | None] = mapped_column(default=None)  # uploads only
    raw_records_cnt: Mapped[int] = mapped_column(default=0)
    accepted_records_cnt: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    annual_records: Mapped[list["AnnualRecord"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        init=False,
        repr=False,
    )

    __table_args__ = (
        CheckConstraint("raw_records_cnt >= 0", name="ck_dataset_raw_cnt"),
        CheckConstraint(
            "accepted_records_cnt >= 0 AND accepted_records_cnt <= raw_records_cnt",
            name="ck_dataset_accepted_cnt",
        ),
    )


class Facility(Base):
    """A plant. The primary key IS the EPA facility ID (ORISPL code)."""

    __tablename__ = "facility"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(index=True)  # two-letter code, e.g. "KY"
    county: Mapped[str | None] = mapped_column(index=True, default=None)
    latitude: Mapped[float | None] = mapped_column(default=None)
    longitude: Mapped[float | None] = mapped_column(default=None)
    source_category: Mapped[str | None] = mapped_column(default=None)

    units: Mapped[list["Unit"]] = relationship(
        back_populates="facility",
        cascade="all, delete-orphan",
        init=False,
        repr=False,
    )

    __table_args__ = (
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name="ck_facility_lat"
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="ck_facility_lon",
        ),
    )


class Unit(Base):
    """A boiler / turbine at a facility."""

    __tablename__ = "unit"

    internal_id: Mapped[int] = mapped_column(init=False, primary_key=True)
    facility_id: Mapped[int] = mapped_column(
        ForeignKey("facility.id", ondelete="CASCADE")
    )
    # EPA unit IDs repeat across facilities ("1" exists everywhere) and can
    # contain letters ("CT1", "6A"), so this is text and is only unique
    # together with facility_id.
    epa_unit_id: Mapped[str]
    type: Mapped[str | None] = mapped_column(index=True, default=None)
    primary_fuel: Mapped[str | None] = mapped_column(index=True, default=None)
    secondary_fuel: Mapped[str | None] = mapped_column(default=None)
    operating_date: Mapped[date | None] = mapped_column(default=None)
    retirement_date: Mapped[date | None] = mapped_column(default=None)

    facility: Mapped["Facility"] = relationship(
        back_populates="units", init=False, repr=False
    )
    annual_records: Mapped[list["AnnualRecord"]] = relationship(
        back_populates="unit",
        cascade="all, delete-orphan",
        init=False,
        repr=False,
    )

    __table_args__ = (
        # One EPA unit ID per facility.
        UniqueConstraint("facility_id", "epa_unit_id", name="uq_unit_facility_epa"),
        # Redundant on purpose: lets annual_record point at (facility, unit)
        # as a pair, so a record can never name a unit from another facility.
        UniqueConstraint("facility_id", "internal_id", name="uq_unit_facility_key"),
        CheckConstraint(
            "retirement_date IS NULL OR operating_date IS NULL "
            "OR retirement_date >= operating_date",
            name="ck_unit_dates",
        ),
    )


class AnnualRecord(Base):
    """One row per unit per reporting year."""

    __tablename__ = "annual_record"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("dataset.id", ondelete="CASCADE"), index=True
    )
    facility_id: Mapped[int]
    unit_key: Mapped[int]  # -> unit.internal_id
    reporting_year: Mapped[int] = mapped_column(index=True)

    # Measurements are optional: the API leaves many blank (steam load is
    # empty for most units) and "blank" must not be stored as zero.
    operating_time: Mapped[float | None] = mapped_column(default=None)  # hours
    gross_load: Mapped[float | None] = mapped_column(default=None)  # MWh
    steam_load: Mapped[float | None] = mapped_column(default=None)  # 1000 lb
    heat_input: Mapped[float | None] = mapped_column(default=None)  # MMBtu
    co2_mass: Mapped[float | None] = mapped_column(default=None)  # short tons
    so2_mass: Mapped[float | None] = mapped_column(default=None)  # short tons
    nox_mass: Mapped[float | None] = mapped_column(default=None)  # short tons
    so2_controls: Mapped[str | None] = mapped_column(Text, default=None)
    nox_controls: Mapped[str | None] = mapped_column(Text, default=None)
    pm_controls: Mapped[str | None] = mapped_column(Text, default=None)
    program_code: Mapped[str | None] = mapped_column(Text, default=None)

    dataset: Mapped["Dataset"] = relationship(
        back_populates="annual_records", init=False, repr=False
    )
    unit: Mapped["Unit"] = relationship(
        back_populates="annual_records", init=False, repr=False
    )
    facility: Mapped["Facility"] = relationship(
        viewonly=True, init=False, repr=False
    )

    __table_args__ = (
        # facility_id and unit_key must match a real unit AS A PAIR.
        ForeignKeyConstraint(
            ["facility_id", "unit_key"],
            ["unit.facility_id", "unit.internal_id"],
            ondelete="CASCADE",
            name="fk_annual_unit",
        ),
        ForeignKeyConstraint(
            ["facility_id"], ["facility.id"], ondelete="CASCADE", name="fk_annual_fac"
        ),
        # CHANGED: uniqueness now includes dataset_id. The same unit-year may exist
        # once in EACH dataset (different versions), but never twice in one dataset.
        UniqueConstraint(
            "unit_key", "reporting_year", "dataset_id", name="uq_unit_year_dataset"
        ),
        CheckConstraint("reporting_year BETWEEN 1990 AND 2100", name="ck_year"),
        CheckConstraint(
            "operating_time IS NULL OR operating_time BETWEEN 0 AND 8784",
            name="ck_operating_time",
        ),
        CheckConstraint(
            "(gross_load IS NULL OR gross_load >= 0) AND "
            "(steam_load IS NULL OR steam_load >= 0) AND "
            "(heat_input IS NULL OR heat_input >= 0) AND "
            "(co2_mass IS NULL OR co2_mass >= 0) AND "
            "(so2_mass IS NULL OR so2_mass >= 0) AND "
            "(nox_mass IS NULL OR nox_mass >= 0)",
            name="ck_measurements_nonneg",
        ),
        Index("idx_annual_facility_year", "facility_id", "reporting_year"),
        Index("idx_annual_co2", "co2_mass"),
        Index("idx_annual_so2", "so2_mass"),
        Index("idx_annual_nox", "nox_mass"),
        Index("idx_annual_heat", "heat_input"),
        Index("idx_annual_load", "gross_load"),
        Index("idx_annual_optime", "operating_time"),
    )


# ---- lookup tables the CAMPD client uses for dropdown lists ----------------


class StateOrTerritory(Base):
    __tablename__ = "state_or_territory"

    code: Mapped[str] = mapped_column(primary_key=True)  # "KY"
    name: Mapped[str] = mapped_column(unique=True)
    epa_region: Mapped[int]


class FuelType(Base):
    __tablename__ = "fuel_type"

    code: Mapped[str] = mapped_column(primary_key=True)  # CAMPD codes are text
    name: Mapped[str]
    group_code: Mapped[str | None] = mapped_column(default=None)

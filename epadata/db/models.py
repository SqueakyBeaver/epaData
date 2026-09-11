from datetime import datetime, timedelta

from sqlalchemy import (
    JSON,
    Engine,
    ForeignKey,
    Index,
    UniqueConstraint,
    create_engine,
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    type_annotation_map = {
        list[int]: JSON,
        list[str]: JSON,
    }


class Dataset(Base):
    __tablename__ = "dataset"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    data_source: Mapped[str]  # What is this for???
    reporting_years: Mapped[list[int]]
    retrieval_date: Mapped[datetime] = mapped_column(insert_default=func.now())
    source_location: Mapped[str]  # Filename or URL
    raw_records_cnt: Mapped[int] = mapped_column(default=0)
    approved_records_cnt: Mapped[int] = mapped_column(default=0)
    notes: Mapped[list[str]]

    annual_records: Mapped[list["AnnualRecord"]] = relationship(
        "AnnualRecord", back_populates="dataset", cascade="all, delete-orphan"
    )


class Facility(Base):
    __tablename__ = "facility"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    state: Mapped[str]  # maybe make this another table?
    county: Mapped[str]
    latitude: Mapped[str]
    longitude: Mapped[str]
    source_category: Mapped[str]  # ???

    units: Mapped[list["Unit"]] = relationship(
        "Unit", back_populates="facility", cascade="all, delete-orphan"
    )
    annual_records: Mapped[list["AnnualRecord"]] = relationship(
        "AnnualRecord", back_populates="facility"
    )


class Unit(Base):
    __tablename__ = "unit"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    epa_id: Mapped[int]
    facility_id = mapped_column(ForeignKey("facility.id", ondelete="CASCADE"))
    type: Mapped[str]
    primary_fuel: Mapped[str]
    secondary_fuel: Mapped[str | None]
    operating_date: Mapped[datetime]
    retirement_date: Mapped[datetime | None]

    facility: Mapped["Facility"] = relationship("Facility", back_populates="units")
    annual_records: Mapped[list["AnnualRecord"]] = relationship(
        "AnnualRecord", back_populates="unit", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("facility_id", "epa_id", name="uq_facility_unit"),
    )


class AnnualRecord(Base):
    __tablename__ = "annual_record"

    id: Mapped[int] = mapped_column(primary_key=True)

    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    facility_id: Mapped[int] = mapped_column(
        ForeignKey("facility.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("unit.id", ondelete="CASCADE"), nullable=False, index=True
    )

    reporting_year: Mapped[int]
    operating_time: Mapped[timedelta]
    gross_load: Mapped[int]
    steam_load: Mapped[int]
    heat_input: Mapped[int]
    co2_mass: Mapped[int]
    so2_mass: Mapped[int]
    nox_mass: Mapped[int]
    so2_controls: Mapped[str]
    nox_controls: Mapped[str]
    pm_controls: Mapped[str]
    program_code: Mapped[str]

    dataset: Mapped["Dataset"] = relationship(
        "Dataset", back_populates="annual_records"
    )
    facility: Mapped["Facility"] = relationship(
        "Facility", back_populates="annual_records"
    )
    unit: Mapped["Unit"] = relationship("Unit", back_populates="annual_records")

    __table_args__ = (
        UniqueConstraint(
            "unit_id", "reporting_year", "dataset_id", name="uq_unit_year_dataset"
        ),
        Index("idx_annual_facility_year", "facility_id", "reporting_year"),
    )


if __name__ == "__main__":
    engine = create_engine("sqlite://", echo=True)

    Base.metadata.create_all(engine)

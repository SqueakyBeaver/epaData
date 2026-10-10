import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Self

import pandas as pd
from pandas.errors import ParserError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

CURRENT_YEAR = datetime.now().year


class DuplicateRowError(Exception):
    """NEW: a row repeats a facility + unit + year that appeared earlier in the same file."""

    def __init__(self, first_row: int):
        self.first_row = first_row


class FacilityInfoRecordModel(BaseModel):
    """
    Pydantic model for validating uploaded EPA CAMPD facility attribute
    and annual operating/emissions data records.
    The Data should be formatted the same as downloading facility information bulk data.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True, populate_by_name=True, extra="ignore"
    )

    # Required Location & Identification
    state: str = Field(
        ...,
        alias="State",
        min_length=2,
        max_length=2,
        description="Two-letter US state postal code",
    )
    facility_name: str = Field(..., alias="Facility Name", min_length=1)
    facility_id: int = Field(
        ..., alias="Facility ID", ge=1, description="ORISPL facility code"
    )
    unit_id: str = Field(..., alias="Unit ID", min_length=1)
    reporting_year: int = Field(..., alias="Year", ge=1995, le=CURRENT_YEAR)

    # Geographic Coordinates
    latitude: float = Field(..., alias="Latitude", ge=-90.0, le=90.0)
    longitude: float = Field(..., alias="Longitude", ge=-180.0, le=180.0)

    # Regional & Administrative Info
    county: str = Field(..., alias="County", min_length=1)
    county_code: str | None = Field(None, alias="County Code")
    fips_code: int | None = Field(None, alias="FIPS Code")
    epa_region: int | None = Field(None, alias="EPA Region", ge=1, le=10)
    nerc_region: str | None = Field(None, alias="NERC Region")
    source_category: str | None = Field(None, alias="Source Category")
    owner_operator: str | None = Field(None, alias="Owner/Operator")
    primary_rep_info: str | None = Field(None, alias="Primary Rep Info")

    # Regulatory Program & Unit Metadata
    program_code: str | None = Field(None, alias="Program Code")
    unit_type: str = Field(..., alias="Unit Type")
    primary_fuel_type: str | None = Field(..., alias="Primary Fuel Type")
    secondary_fuel_type: str | None = Field(None, alias="Secondary Fuel Type")
    operating_status: str | None = Field(None, alias="Operating Status")
    commercial_operation_date: date | None = Field(
        None, alias="Commercial Operation Date"
    )

    # Environmental Control Equipment
    so2_phase: str | None = Field(None, alias="SO2 Phase")
    nox_phase: str | None = Field(None, alias="NOx Phase")
    so2_controls: str | None = Field(None, alias="SO2 Controls")
    nox_controls: str | None = Field(None, alias="NOx Controls")
    pm_controls: str | None = Field(None, alias="PM Controls")
    hg_controls: str | None = Field(None, alias="Hg Controls")

    # Operational Capacity & Stack Information
    max_hourly_hi_rate: float | None = Field(
        None,
        alias="Max Hourly HI Rate (mmBtu/hr)",
        ge=0.0,
        description="Maximum hourly heat input rate in mmBtu/hr",
    )
    associated_stacks: str | None = Field(None, alias="Associated Stacks")
    associated_generators: str | None = Field(
        None, alias="Associated Generators & Nameplate Capacity (MWe)"
    )

    # Optional Annual Operating & Emissions Metrics (if multi-year dataset includes them)
    operating_time: float | None = Field(
        None, alias="Operating Time", ge=0.0, le=8784.0
    )
    gross_load: float | None = Field(None, alias="Gross Load", ge=0.0)
    steam_load: float | None = Field(None, alias="Steam Load", ge=0.0)
    heat_input: float | None = Field(None, alias="Heat Input", ge=0.0)
    co2_mass: float | None = Field(None, alias="CO2 Mass", ge=0.0)
    so2_mass: float | None = Field(None, alias="SO2 Mass", ge=0.0)
    nox_mass: float | None = Field(None, alias="NOx Mass", ge=0.0)

    @field_validator("state")
    @classmethod
    def validate_state_uppercase(cls, v: str) -> str:
        """Ensures state codes are standardized uppercase."""
        return v.upper()

    @field_validator("unit_id", mode="before")
    @classmethod
    def normalize_unit_id(cls, value: Any) -> Any:
        """CSV readers may infer numeric-looking EPA unit IDs as integers."""
        return None if value is None else str(value).strip()

    @field_validator("*", mode="before")
    @classmethod
    def sanitize_nan_and_empty(cls, value: Any) -> Any:
        if pd.isna(value):
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @classmethod
    def validate_from_file(
        cls, file_path: str, column_mapping: dict[str, str] | None = None
    ) -> tuple[list[Self], list[Any]]:
        prepared = cls.prepare_file(file_path, column_mapping)
        return prepared["valid_records"], prepared["invalid_records"]

    @classmethod
    def prepare_file(
        cls, file_path: str, column_mapping: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Read a file, apply a source-column mapping, and validate every row.

        column_mapping maps uploaded column names to the model aliases used
        by the EPA schema. Unknown columns are deliberately retained in the
        preview so the upload page can let a user map them or ignore them.
        """
        ext = Path(file_path).suffix.lower()

        df: pd.DataFrame

        try:
            if ext == ".csv":
                df = pd.read_csv(file_path)
            elif ext in (".xlsx", ".xls"):
                df = pd.read_excel(file_path)
            else:
                raise ValueError(
                    f"Unsupported file format '{ext}'. Expected .csv, .xlsx, or .xls"
                )
        except ParserError as e:
            print(e)
            raise ValueError(
                "An error occured while parsing the file. Ensure it is formatted correctly."
            )

        columns = [str(column) for column in df.columns]
        df.columns = columns
        aliases = {
            field.alias or name: name for name, field in cls.model_fields.items()
        }

        def normalized(value: str) -> str:
            return re.sub(r"[^a-z0-9]", "", value.lower())

        normalized_aliases = {normalized(alias): alias for alias in aliases}
        if column_mapping is None:
            column_mapping = {
                column: normalized_aliases[normalized(column)]
                for column in columns
                if normalized(column) in normalized_aliases
            }
        else:
            column_mapping = {
                source: target
                for source, target in column_mapping.items()
                if source in columns and target in aliases
            }

        unmapped_columns = [
            column for column in columns if column not in column_mapping
        ]
        # Replace NaN values with None so Pydantic handles optional fields correctly.
        records = df.where(pd.notnull(df), None).to_dict(orient="records")
        preview_records = json.loads(
            df.head(100).to_json(orient="records", date_format="iso")
        )

        valid_records = []
        invalid_records = []
        first_seen: dict[tuple, int] = {}  # NEW: (facility, unit, year) -> first row number

        for idx, row in enumerate(records):
            row_number = idx + 2  # Account for header row and 1-based index
            mapped_row = {
                column_mapping[str(source)]: value
                for source, value in row.items()
                if str(source) in column_mapping
            }
            try:
                # Validate row dictionary against Pydantic schema
                validated_row = FacilityInfoRecordModel.model_validate(mapped_row)
                # NEW: the same facility + unit + year twice in one file used to crash the
                # import (database uniqueness rule). Keep the first, report the repeats.
                key = (
                    validated_row.facility_id,
                    validated_row.unit_id,
                    validated_row.reporting_year,
                )
                if key in first_seen:
                    raise DuplicateRowError(first_seen[key])
                first_seen[key] = row_number
                valid_records.append(validated_row)
            except DuplicateRowError as dup:
                invalid_records.append(
                    {
                        "row_number": row_number,
                        "facility_id": mapped_row.get("Facility ID"),
                        "unit_id": mapped_row.get("Unit ID"),
                        "year": mapped_row.get("Year"),
                        "errors": [
                            {
                                "type": "duplicate",
                                "loc": ("Facility ID", "Unit ID", "Year"),
                                "msg": f"Duplicate of row {dup.first_row}: same facility, unit and year",
                            }
                        ],
                        "raw": mapped_row,
                        "as_json": json.dumps(mapped_row, default=str),
                    }
                )
            except ValidationError as err:
                # Capture error details for the data-quality report
                invalid_records.append(
                    {
                        "row_number": row_number,
                        "facility_id": mapped_row.get("Facility ID"),
                        "unit_id": mapped_row.get("Unit ID"),
                        "year": mapped_row.get("Year"),
                        "errors": err.errors(),
                        "raw": mapped_row,
                        "as_json": json.dumps(mapped_row, default=str),
                    }
                )

        return {
            "columns": columns,
            "mapping_options": list(aliases),
            "column_mapping": column_mapping,
            "unmapped_columns": unmapped_columns,
            "preview_records": preview_records,
            "valid_records": valid_records,
            "invalid_records": invalid_records,
            "row_count": len(records),
        }


if __name__ == "__main__":
    import pandas as pd
    from pydantic import ValidationError

    valid, invalid = FacilityInfoRecordModel.validate_from_file(
        "epadata/data/facility-2025.csv"
    )

    print(len(valid), len(invalid))
    print(valid[0])
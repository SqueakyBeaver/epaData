from collections.abc import Sequence
from datetime import date
from os import getenv
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import select

from epadata.db import Session
from epadata.db.models import StateOrTerritory


class CAMPDClient:
    def __init__(self):
        load_dotenv()
        self.api_key = str(getenv("CAMPD_API_KEY"))

        if not self.api_key:
            raise ValueError("No CAMPD_API_KEY found in environment variables or .env")

    def send_request(
        self,
        endpoint: str,
        *,
        _headers: dict[str, str | bytes] | None = None,
        **params,
    ) -> list[dict[str, str]]:
        if not "api_key" in params:
            params["api_key"] = self.api_key

        headers: dict[str, str | bytes] = {"x-api-key": self.api_key}
        if _headers:
            headers.update(_headers)

        for k, v in params.copy().items():
            if not k or not v:
                params.pop(k)

        resp = requests.get(
            url=f"https://api.epa.gov/easey/{endpoint}",
            headers=headers,
            params=params,
            stream=True,
        )

        return resp.json()["items"]

    def get_state_codes(self) -> Sequence[StateOrTerritory]:
        """
        Get state codes that CAMPD uses.
        Returned format is an array of:
        { "stateCode": "XX", "stateName": "full state name", "epaRegion": "##" }
        """
        with Session() as session:
            states = session.scalars(select(StateOrTerritory)).all()

            if len(states) >= 50:
                return states

        return [
            StateOrTerritory(
                code=i["stateCode"], name=i["stateName"], epa_region=int(i["epaRegion"])
            )
            for i in self.send_request(
                endpoint="master-data-mgmt/state-codes",
            )
        ]

    def get_fuel_type_codes(self) -> list[dict[str, str]]:
        """
        Get fuel type codes that CAMPD uses.
        Returned format is an array of:
        { "fuelTypeCode": "XX", "fuelTypeDescription": "Fuel name",
          "fuelGroupCode": "XXXXXXXX", "fuelGroupDescription": "Fuel Group Name" }
        """
        return self.send_request(endpoint="master-data-mgmt/fuel-type-codes")

    def get_facilities(self) -> list[dict[str, str]]:
        """
        Get facility names and codes that CAMPD has data on.
        Returned format is an array of:
        { 'facilityRecordId': ####, 'facilityId': ####,
          'facilityName': 'facility name', 'stateCode': 'XX' }
        """
        return self.send_request(endpoint="facilities-mgmt/facilities")

    def _get_facility_attributes_for_year(self, year: str | tuple[str, str]):
        """
        Get applicable facility attributes for the given year(s).
        If `year` is a tuple, it should be in the format [start, stop],
          and the attributes for all years from start to stop (inclusive) will be fetched
        If a facility does not have data for that time, it is not included.
        NOTE: This returns A LOT of data.
        """
        if isinstance(year, tuple):
            year = "|".join(str(i) for i in range(int(year[0]), int(year[1]) + 1))

        res = self.send_request(
            endpoint="facilities-mgmt/facilities/attributes/applicable",
            year=year,
        )
        return res

    def get_filtered_facilities(
        self, years: str | tuple[str, str], **filters
    ) -> pd.DataFrame:
        """
        Get a list of facility codes and names that fulfill the given filters
        NOTE: the filters column names MUST be in camelCase i.e. stateCode="KY"
        """
        attrs = pd.json_normalize(self._get_facility_attributes_for_year(years))
        facilities = pd.json_normalize(self.get_facilities())

        mask = pd.Series(True, index=facilities.index)

        mask &= facilities["facilityId"].isin(attrs["facilityId"]).drop_duplicates()

        for column, value in filters.items():
            mask &= facilities[column].eq(value)

        return facilities.loc[
            mask,
            ["facilityId", "facilityName"],
        ]

    def get_facility_emissions_hourly(
        self, facility_id: int, start_date: date, end_date: date
    ):
        # TODO I think he wants this information... ugh
        # https://api.epa.gov/easey/emissions-mgmt/emissions/apportioned/hourly?stateCode=KY&unitFuelType=Coal&beginDate=2025-09-28&endDate=2025-10-31&operatingHoursOnly=true&page=1&perPage=5
        pass

    def get_facility_data(
        self,
        years: str,
        facility_id: str | None,
        state_code: str | None,
        fuel_code: str | None,
    ) -> pd.DataFrame:
        resp = self.send_request(
            "facilities-mgmt/facilities/attributes",
            year=years,
            facilityId=facility_id,
            stateCode=state_code,
            unitFuelType=fuel_code,
            page=1,
            perPage=100,
        )

        # TODO: Cache in database

        return pd.json_normalize(resp)


if __name__ == "__main__":
    c = CAMPDClient()

    codes = c.get_filtered_facilities(("2020", "2022"))
    print(codes)

from collections.abc import Sequence
from os import getenv

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

    def get_facilities_for_year(self, year: str | tuple[str, str]) -> pd.DataFrame:
        """
        Get a list of facility codes and names that have data in a given year
        """
        attrs = pd.json_normalize(self._get_facility_attributes_for_year(year))
        facilities = pd.json_normalize(self.get_facilities())

        return facilities.loc[
            facilities["facilityId"].isin(attrs["facilityId"].drop_duplicates()),
            ["facilityId", "facilityName"],
        ]


if __name__ == "__main__":
    c = CAMPDClient()

    codes = c.get_facilities_for_year(("2020", "2022"))
    print(codes)

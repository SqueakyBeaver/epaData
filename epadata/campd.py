from os import getenv
from typing import Any

import requests
from dotenv import load_dotenv


class CAMPDClient:
    def __init__(self):
        load_dotenv()
        self.api_key = getenv("CAMPD_API_KEY")

    def send_request(
        self, endpoint: str, _headers: dict[str, Any] | None = None, **params
    ):
        if not "api_key" in params:
            params["api_key"] = self.api_key

        headers = {"x-api-key": self.api_key}
        if _headers:
            headers.update(_headers)

        resp = requests.get(
            url=f"https://api.epa.gov/easey/{endpoint}", headers=headers, params=params
        )

        return resp

    # TODO: Add different functions for requesting facilities and stuff idk
    
    def get_state_codes(self) -> list[dict[str, str]]:
        """
        Get state codes that CAMPD uses. Returned format is an array of:
        { "stateCode": "XX", "stateName": "full state name", "epaRegion": "##" }
        """
        resp = self.send_request(
            endpoint="master-data-mgmt/state-codes",
        )

        return resp.json()["items"]

    def get_fuel_type_codes(self) -> list[dict[str, str]]:
        """
        Get fuel type codes that CAMPD uses. Returned format is an array of:
        { "fuelTypeCode": "XX", "fuelTypeDescription": "Fuel name",
          "fuelGroupCode": "XXXXXXXX", "fuelGroupDescription": "Fuel Group Name" }
        """
        resp = self.send_request(endpoint="master-data-mgmt/fuel-type-codes")

        return resp.json()["items"]


if __name__ == "__main__":
    c = CAMPDClient()

    codes = c.get_fuel_type_codes()
    print(codes)

import time  # NEW: short pauses before retrying a failed request
from collections.abc import Sequence
from datetime import date
from os import getenv

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import select

from epadata.db import Session
from epadata.db.models import StateOrTerritory


# NEW: raised when the EPA answers "429 Too Many Requests". The EPA allows each API key a limited
# number of requests per hour. retry_after is how many seconds the EPA asked us to wait (or None).
class RateLimitError(RuntimeError):
    def __init__(self, retry_after=None):
        super().__init__("EPA rate limit exceeded")
        self.retry_after = retry_after


def _seconds(value):
    """NEW: '120' -> 120. Anything else (missing, a date, junk) -> None."""
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


class CAMPDClient:
    def __init__(self):
        load_dotenv()
        # CHANGED: str(getenv(...)) turned a MISSING key into the text "None", which is never
        # empty, so the check below could never fire. Check the real value instead.
        self.api_key = getenv("CAMPD_API_KEY") or ""

        if not self.api_key:
            raise ValueError(
                "No CAMPD_API_KEY found in environment variables or .env")

        # NEW: used by the "load all data" job (bulkload.py)
        self.before_request = None   # called just before every request (to pace them)
        self.rate_remaining = None   # requests the EPA says are left (when it tells us)
        self.rate_limit = None       # the EPA's own limit per hour (when it tells us)

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

        # CHANGED: the request is tried up to 3 times when the connection drops or the EPA has a
        # temporary server problem (500-504). A timeout was added too: before, one stuck request
        # could hang the page forever.
        for attempt in range(3):
            if self.before_request:
                self.before_request()
            try:
                resp = requests.get(
                    url=f"https://api.epa.gov/easey/{endpoint}",
                    headers=headers,
                    params=params,
                    stream=True,
                    timeout=(15, 180),  # seconds to connect, seconds of silence while reading
                )
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
                continue

            # NEW: api.data.gov (which fronts this API) reports the hourly allowance in headers
            left = _seconds(resp.headers.get("X-RateLimit-Remaining"))
            if left is not None:
                self.rate_remaining = left
            allowed = _seconds(resp.headers.get("X-RateLimit-Limit"))
            if allowed is not None:
                self.rate_limit = allowed

            if resp.status_code == 429:
                # NEW: do NOT keep hammering the API. Tell the caller how long to wait.
                raise RateLimitError(
                    _seconds(resp.headers.get("Retry-After") or resp.headers.get("retryAfter"))
                )
            if resp.status_code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            break

        if not resp.ok:
            raise RuntimeError(
                f"CAMPD request failed ({resp.status_code}): {resp.text[:300]}"
            )

        resp_json = resp.json()

        if isinstance(resp_json, list):
            return resp_json
        elif isinstance(resp_json, dict):
            return resp_json["items"]
        else:
            print(
                f"Got response JSON of type {type(resp_json)} for request to {endpoint}. Returning as is I guess")
            return resp_json

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
                code=i["stateCode"], name=i["stateName"], epa_region=int(
                    i["epaRegion"])
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

    def get_program_codes(self) -> list[dict[str, str]]:
        """
        Schema:
        {
            "programCode": "string",
            "programDescription": "string",
            "compParameter": "string",
            "programGroupCode": "string",
            "programGroupDescription": "string",
            "ozoneIndicator": true,
            "annualIndicator": true,
            "emissionsUIFilter": true,
            "allowanceUIFilter": true,
            "complianceUIFilter": true,
            "retiredIndicator": true,
            "tradingEndDate": "string"
        }
        """
        return self.send_request(endpoint="master-data-mgmt/program-codes")

    def get_unit_type_codes(self) -> list[dict[str, str]]:
        """
        Schema:
        {
            "unitTypeCode": "string",
            "unitTypeDescription": "string",
            "unitTypeGroupCode": "string",
            "unitTypeGroupDescription": "string",
            "sortOrder": "string"
        }
        """
        return self.send_request(endpoint="master-data-mgmt/unit-type-codes")

    def get_control_codes(self) -> list[dict[str, str]]:
        """
        Schema:
        {
            "controlCode": "string",
            "controlDescription": "string",
            "controlEquipParamCode": "string",
            "controlEquipParamDescription": "string"
        }
        """
        return self.send_request(endpoint="master-data-mgmt/control-codes")

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
            year = "|".join(str(i)
                            for i in range(int(year[0]), int(year[1]) + 1))

        res = self.send_request(
            endpoint="streaming-services/facilities/attributes",
            year=year,
        )
        return res

    def search_facilities(
        self, years: str | tuple[str, str], **filters
    ) -> pd.DataFrame:
        """
        Get a list of facility codes and names that fulfill the given filters
        NOTE: the filters column names MUST be in camelCase i.e. stateCode="KY"
        """
        attrs = pd.json_normalize(
            self._get_facility_attributes_for_year(years))
        facilities = pd.json_normalize(self.get_facilities())

        mask = pd.Series(True, index=facilities.index)

        mask &= facilities["facilityId"].isin(
            attrs["facilityId"].drop_duplicates())

        for column, value in filters.items():
            mask &= facilities[column].eq(value)

        return facilities.loc[
            mask,
            ["facilityId", "facilityName"],
        ]

    def get_facility_data(
        self,
        years: str,
        facility_id: str | None,
        state_code: str | None,
        fuel_code: str | None,
    ) -> pd.DataFrame:
        expanded = set()
        for part in str(years).replace(" ", "").split(","):
            start, _, end = part.partition("-")
            if start.isdigit() and end.isdigit():
                expanded.update(range(int(start), int(end) + 1))
            elif start.isdigit():
                expanded.add(int(start))
        years = "|".join(str(y) for y in sorted(expanded))
        resp = self.send_request(
            "streaming-services/facilities/attributes",
            year=years,
            facilityId=facility_id,
            stateCode=state_code,
            unitFuelType=fuel_code,
            page=1,
            perPage=100,
        )

        # TODO: Cache in database

        return pd.json_normalize(resp)

    def _get_all_pages(self, endpoint: str, per_page: int = 100, **params):
        """Ask for page 1, 2, 3 ... until the API returns an empty page."""
        rows, previous = [], None
        for page in range(1, 1001):  # hard stop so a misbehaving API can't loop forever
            batch = self.send_request(
                endpoint, page=page, perPage=per_page, **params)
            if not batch or batch == previous:
                break
            rows.extend(batch)
            previous = batch
        return rows

    # CHANGED: both methods now take unit_type, control_technology and source_category too.
    # The EPA wants their full descriptions, e.g. "Combustion turbine", "Wet Limestone",
    # "Electric Utility" (several could be joined with "|").

    def get_annual_emissions(
        self,
        years,
        state_code=None,
        facility_id=None,
        fuel_code=None,
        unit_type=None,
        control_technology=None,
        source_category=None,
    ):
        """One dict per unit per year, WITH emissions numbers. `years` is a list of ints.

        source_category is accepted but NOT sent: this endpoint is not documented to take it.
        The route narrows the rows by source category using get_facility_attributes instead.
        """
        rows = []
        for year in years:
            rows += self.send_request(
                "streaming-services/emissions/apportioned/annual",
                year=year,
                stateCode=state_code,
                facilityId=facility_id,
                unitFuelType=fuel_code,
                unitType=unit_type,
                controlTechnologies=control_technology,
            )
        return rows

    def get_annual_emissions_all_years(self, years, state_code=None):
        """NEW: ONE request for all of these years (years joined with "|", as the attributes call does).

        The EPA's "streaming-services" endpoints are made to send a lot of data in one request, and
        they seem to allow far fewer requests than the older paged ones. Asking once per year (what
        get_annual_emissions does) used up the allowance within minutes.
        """
        return self.send_request(
            "streaming-services/emissions/apportioned/annual",
            year="|".join(str(y) for y in years),
            stateCode=state_code,
        )

    def get_facility_attributes_all_years(self, years, state_code=None):
        """NEW: one request for ALL of these years (the EPA takes years joined with "|").

        The normal get_facility_attributes below asks once per year. For the "load all data" job
        that would double the number of requests, and the EPA limits requests per hour.
        """
        return self.send_request(
            "streaming-services/facilities/attributes",
            year="|".join(str(y) for y in years),
            stateCode=state_code,
        )

    def get_facility_attributes(
        self,
        years,
        state_code=None,
        facility_id=None,
        fuel_code=None,
        unit_type=None,
        control_technology=None,
        source_category=None,
    ):
        """One dict per unit per year, WITH county / lat / long / operation date."""
        rows = []
        for year in years:
            rows += self.send_request(
                "streaming-services/facilities/attributes",
                year=year,
                stateCode=state_code,
                facilityId=facility_id,
                unitFuelType=fuel_code,
                unitType=unit_type,
                controlTechnologies=control_technology,
                sourceCategory=source_category,
            )
        return rows


if __name__ == "__main__":
    c = CAMPDClient()

    codes = c.search_facilities(("2020", "2022"))
    print(codes)
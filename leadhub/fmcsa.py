"""Gateway to FMCSA's public open-data API.

The only module that knows those endpoints exist. If FMCSA changes a dataset id
or a field name, the change stops here (Protected Variations).

Datasets used - all free, no key, no login, on data.transportation.gov:
  yu5v-wbh6  Motus AuthHist  - authority status changes, with dates
  inys-ebih  Motus Carrier   - identity, address, phone, authority type/status
  c5y8-a4uz  Motus Insur     - insurance policies
  az4n-8mr2  Company Census  - cell phone, company officer, fleet size (daily)
  kjg3-diqy  SMS Census      - email address (monthly)
"""
import datetime
import json
import ssl
import time
import urllib.parse
import urllib.request

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:                                    # pragma: no cover
    _SSL = ssl.create_default_context()

BASE = "https://data.transportation.gov/resource/"
DS_AUTH = "yu5v-wbh6"
DS_CARRIER = "inys-ebih"
DS_INSURANCE = "c5y8-a4uz"
DS_CENSUS = "az4n-8mr2"
DS_EMAIL = "kjg3-diqy"

TRUCK_AUTHORITY = ("Motor Carrier of Property", "Motor Carrier of Household Goods",
                   "Enterprise Motor Carrier")
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
                "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
                "WI WY DC PR VI".split())


class FmcsaClient:
    def __init__(self, timeout=180, retries=3):
        self.timeout, self.retries = timeout, retries

    def query(self, dataset, params):
        url = BASE + dataset + ".json?" + urllib.parse.urlencode(params)
        last = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "LeadHub/2.0"})
                with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL) as r:
                    return json.load(r)
            except Exception as exc:                 # transient network / 5xx
                last = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"FMCSA request failed: {last}")

    def lookup_many(self, dataset, key, ids, select, batch=200):
        """Fetch rows whose `key` is in `ids`, in URL-safe batches."""
        out, ids = {}, list(ids)
        for i in range(0, len(ids), batch):
            chunk = [x for x in ids[i:i + batch] if x]
            if not chunk:
                continue
            values = ",".join("'" + str(v) + "'" for v in chunk)
            for row in self.query(dataset, {"$select": select,
                                            "$where": f"{key} IN ({values})",
                                            "$limit": batch * 3}):
                out[str(row.get(key))] = row
        return out

    # ------------------------------------------------------------- steps
    def recent_active_authorities(self, days):
        """{docket_number: date_it_went_active} for the last `days` days."""
        since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
        rows = self.query(DS_AUTH, {
            "$select": "docket_number,usdot_number,status_change_date",
            "$where": f"status_change_date >= '{since}' AND op_auth_status='Active'",
            "$order": "status_change_date DESC", "$limit": 50000})
        out = {}
        for r in rows:
            dk = r.get("docket_number")
            if dk and dk not in out:
                out[dk] = r.get("status_change_date", "")
        return out

    def carriers(self, dockets):
        return self.lookup_many(
            DS_CARRIER, "docket_number", dockets,
            "docket_number,usdot_number,legal_name,dba_name,op_auth_type,op_auth_status,"
            "bus_street_po,bus_city,bus_state_code,bus_zip_code,bus_ctry_code,bus_telno")

    def census(self, dots):
        return self.lookup_many(
            DS_CENSUS, "dot_number", dots,
            "dot_number,cell_phone,company_officer_1,phone,power_units,total_drivers")

    def emails(self, dots):
        return self.lookup_many(DS_EMAIL, "dot_number", dots, "dot_number,email_address")

    def insurance(self, dockets):
        return self.lookup_many(DS_INSURANCE, "docket_number", dockets,
                                "docket_number,insurance_company_name,max_cov_amount")

    @staticmethod
    def is_us_trucking_carrier(rec) -> bool:
        return (str(rec.get("op_auth_type", "")).startswith(TRUCK_AUTHORITY)
                and rec.get("op_auth_status") == "Active"
                and (rec.get("bus_ctry_code") or "") == "US"
                and (rec.get("bus_state_code") or "") in US_STATES)

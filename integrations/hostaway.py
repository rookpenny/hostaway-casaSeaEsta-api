import requests
from integrations.base import BasePMSIntegration
import os

AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")

class HostawayIntegration(BasePMSIntegration):
    def get_token(self):
        url = "https://api.hostaway.com/v1/accessTokens"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.credentials["client_id"],
            "client_secret": self.credentials["secret"],
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = requests.post(url, data=data, headers=headers)
        response.raise_for_status()
        return response.json()["access_token"]

    def fetch_properties(self):
        token = self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = "https://api.hostaway.com/v1/listings"

        response = requests.get(url, headers=headers)
        response.raise_for_status()
        properties = response.json().get("result", [])

        # Filter by account if needed
        filtered = [
            p for p in properties
            if self.credentials["client_id"] in map(str, p.get("accountIds", []))
        ]

        return [{
            "Property Name": p.get("internalListingName"),
            "Hostaway Property ID": str(p.get("id")),
            "Notes": p.get("name"),
            "Active": True
        } for p in filtered]

from integrations.hostaway import HostawayIntegration
from utils.airtable import fetch_pmcs, save_properties_to_airtable

INTEGRATION_MAP = {
    "hostaway": HostawayIntegration,
    # "guesty": GuestyIntegration, (future)
    # "lodgify": LodgifyIntegration, (future)
}

def sync_all_pmcs():
    pmcs = fetch_pmcs()
    for pmc in pmcs:
        fields = pmc.get("fields", {})
        pms = fields.get("PMS Integration", "").lower()
        client_id = fields.get("PMS Client ID")
        secret = fields.get("PMS Secret")

        if not client_id or not secret or pms not in INTEGRATION_MAP:
            continue

        try:
            integration = INTEGRATION_MAP[pms](
                credentials={"client_id": client_id, "secret": secret},
                pmc_record_id=pmc["id"]
            )
            properties = integration.fetch_properties()
            save_properties_to_airtable(properties, pmc["id"])
            print(f"[SYNCED] {len(properties)} properties for {pms}")
        except Exception as e:
            print(f"[ERROR] Failed syncing for {pms}: {e}")

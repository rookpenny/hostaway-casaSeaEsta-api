import os
import requests
from datetime import datetime
from utils.github_sync import sync_pmc_to_github
from dotenv import load_dotenv
from utils.config import LOCAL_CLONE_PATH

from sqlalchemy import create_engine, text
# Set up PostgreSQL connection (via Render or your environment variable)
DATABASE_URL = os.getenv("DATABASE_URL")  # should be in form: postgresql://user:pass@host:port/dbname
engine = create_engine(DATABASE_URL)


load_dotenv()

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Properties table ID
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"         # PMC table ID

def fetch_pmc_lookup():
    """Fetch PMC configs from PostgreSQL and return a dict of account_id -> credentials."""
    lookup = {}

    query = text("""
        SELECT 
            "PMS Account ID" AS account_id,
            "PMS Client ID" AS client_id,
            "PMS Secret" AS client_secret,
            "PMS Integration" AS pms,
            "API Base URL" AS base_url,
            "API Version" AS version,
            "Sync Enabled" AS sync_enabled,
            id AS record_id
        FROM pmcs
        WHERE "PMS Account ID" IS NOT NULL
          AND "PMS Client ID" IS NOT NULL
          AND "PMS Secret" IS NOT NULL
          AND "Sync Enabled" = TRUE;
    """)

    with engine.connect() as conn:
        result = conn.execute(query).fetchall()

        for row in result:
            base_url = row.base_url or default_base_url(row.pms)
            lookup[str(row.account_id)] = {
                "record_id": row.record_id,
                "client_id": row.client_id,
                "client_secret": row.client_secret,
                "pms": row.pms.lower(),
                "base_url": base_url,
                "version": row.version,
            }

    return lookup

def default_base_url(pms):
    pms = pms.lower()
    return {
        "hostaway": "https://api.hostaway.com/v1",
        "guesty": "https://open-api.guesty.com/v1",
        "lodgify": "https://api.lodgify.com/v1"
    }.get(pms, "https://api.example.com/v1")

''' THIS IS AIRTABLE LOOK UP CODE
def fetch_pmc_lookup():
    """Fetch PMC configs from Airtable and return a dict of client_id -> credentials."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    records = response.json().get("records", [])
    lookup = {}

    def default_base_url(pms):
        pms = pms.lower()
        return {
            "hostaway": "https://api.hostaway.com/v1",
            "guesty": "https://open-api.guesty.com/v1",
            "lodgify": "https://api.lodgify.com/v1"
        }.get(pms, "https://api.example.com/v1")  # fallback for future

    for record in records:
        fields = record.get("fields", {})
        account_id = str(fields.get("PMS Account ID", "")).strip()
        client_id = str(fields.get("PMS Client ID", "")).strip()  # ✅ ADDED
        client_secret = str(fields.get("PMS Secret", "")).strip()
        pms = fields.get("PMS Integration", "").strip().lower()
        sync_enabled = fields.get("Sync Enabled", True)

        base_url = fields.get("API Base URL", "").strip() or default_base_url(pms)
        version = fields.get("API Version", "").strip()

        if account_id and client_id and client_secret and sync_enabled:
            lookup[account_id] = {
                "record_id": record["id"],
                "client_id": client_id,  # ✅ ADDED
                "client_secret": client_secret,
                "pms": pms,
                "base_url": base_url,
                "version": version,
            }

    return lookup
'''

def get_access_token(client_id: str, client_secret: str, base_url: str, pms: str) -> str:
    if pms == "hostaway":
        token_url = f"{base_url}/accessTokens"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    elif pms == "guesty":
        token_url = f"{base_url}/auth"
        payload = {
            "clientId": client_id,
            "clientSecret": client_secret
        }
        headers = {"Content-Type": "application/json"}
    else:
        raise Exception(f"Unsupported PMS for auth: {pms}")

    if headers["Content-Type"] == "application/json":
        response = requests.post(token_url, json=payload, headers=headers)
    else:
        response = requests.post(token_url, data=payload, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Token request failed: {response.text}")

    return response.json()["access_token"]

def save_to_postgres(properties, client_id, pmc_record_id, pms, account_id):
    """Save property records to the PostgreSQL 'properties' table."""
    from sqlalchemy import text

    insert_stmt = text("""
        INSERT INTO properties (
            property_name,
            pms_property_id,
            pms_account_id,
            pms_integration,
            sandy_enabled,
            data_folder_path,
            pmc_record_id,
            last_synced
        ) VALUES (
            :property_name,
            :pms_property_id,
            :pms_account_id,
            :pms_integration,
            :sandy_enabled,
            :data_folder_path,
            :pmc_record_id,
            :last_synced
        )
        ON CONFLICT (pms_property_id) DO UPDATE SET
            property_name = EXCLUDED.property_name,
            last_synced = EXCLUDED.last_synced,
            sandy_enabled = EXCLUDED.sandy_enabled,
            data_folder_path = EXCLUDED.data_folder_path;
    """)

    with engine.begin() as conn:
        for prop in properties:
            prop_id = str(prop.get("id"))
            name = prop.get("internalListingName") or prop.get("name")
            folder = ensure_pmc_structure(pmc_name=client_id, property_id=prop_id, property_name=name)

            conn.execute(insert_stmt, {
                "property_name": name,
                "pms_property_id": prop_id,
                "pms_account_id": account_id,
                "pms_integration": pms,
                "sandy_enabled": True,
                "data_folder_path": folder,
                "pmc_record_id": pmc_record_id,
                "last_synced": datetime.utcnow()
            })


def fetch_properties(access_token: str, base_url: str, pms: str):
    """Fetch property list from PMS API using bearer token."""
    url = f"{base_url}/listings" if pms == "hostaway" else f"{base_url}/properties"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch properties: {response.text}")

    if pms == "hostaway":
        return response.json().get("result", [])
    else:
        return response.json().get("properties", [])


def save_to_airtable(properties, client_id, pmc_record_id, pms):
    """Write fetched property records to Airtable and prepare GitHub sync info."""
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    results = []  # ⬅️ make results iterable

    for prop in properties:
        prop_id = str(prop.get("id"))
        name = prop.get("internalListingName") or prop.get("name")

        # ✅ Create folder and collect paths
        base_dir = ensure_pmc_structure(pmc_name=client_id, property_id=prop_id, property_name=name)
        config_path = os.path.join(base_dir, "config.json")
        manual_path = os.path.join(base_dir, "manual.txt")

        # ✅ Build destination-relative paths for GitHub
        rel_config = os.path.join("data", str(client_id), prop_id, "config.json")
        rel_manual = os.path.join("data", str(client_id), prop_id, "manual.txt")

        payload = {
            "fields": {
                "Property Name": name,
                "PMS Property ID": prop_id,
                "PMC Record ID": [pmc_record_id],  # must be a list
                "PMS Integration": pms,
                "Sync Enabled": True,
                "Last Synced": datetime.utcnow().isoformat(),
                "Sandy Enabled": True,
                "Data Folder Path": base_dir
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code in (200, 201):
            results.append({
                "folder": base_dir,
                "files": {
                    rel_config: config_path,
                    rel_manual: manual_path
                }
            })
        else:
            print(f"[ERROR] Failed to save property {name}: {res.text}")

    return results  # ⬅️ now correctly returns a list of result dicts





def sync_properties(account_id: str):
    """Sync a single PMC by account ID and push created folders/files to GitHub."""
    pmcs = fetch_pmc_lookup()
    print(f"[DEBUG] Fetched PMCs: {list(pmcs.keys())}")

    if account_id not in pmcs:
        raise Exception(f"PMC not found for account ID: {account_id}")

    pmc = pmcs[account_id]
    token = get_access_token(
        pmc["client_id"],
        pmc["client_secret"],
        pmc["base_url"],
        pmc["pms"]
    )
    properties = fetch_properties(token, pmc["base_url"], pmc["pms"])

    # ✅ Save to PostgreSQL instead of Airtable
    save_to_postgres(
        properties,
        client_id=pmc["client_id"],
        pmc_record_id=pmc["record_id"],
        pms=pmc["pms"],
        account_id=account_id
    )

    # 🔁 Optional GitHub sync (can keep or disable)
    try:
        for prop in properties:
            name = prop.get("internalListingName") or prop.get("name")
            prop_id = str(prop.get("id"))
            base_dir = ensure_pmc_structure(pmc_name=pmc["client_id"], property_id=prop_id, property_name=name)

            rel_config = os.path.join("data", pmc["client_id"], prop_id, "config.json")
            rel_manual = os.path.join("data", pmc["client_id"], prop_id, "manual.txt")

            sync_pmc_to_github(base_dir, {
                rel_config: os.path.join(base_dir, "config.json"),
                rel_manual: os.path.join(base_dir, "manual.txt")
            })
    except Exception as e:
        print(f"[GITHUB] ⚠️ Failed to push PMC {account_id} to GitHub: {e}")

    print(f"[SYNC] ✅ Saved {len(properties)} properties for {account_id}")
    return len(properties)

    
def save_properties_to_db(properties, client_id, pmc_record_id, pms):
    from sqlalchemy import insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from database import SessionLocal
    from models import Property

    session = SessionLocal()
    saved = 0

    try:
        for prop in properties:
            prop_id = str(prop.get("id"))
            name = prop.get("internalListingName") or prop.get("name")

            stmt = pg_insert(Property).values(
                pmc_id=pmc_record_id,
                pms_integration=pms,
                pms_property_id=prop_id,
                name=name,
                sync_enabled=True,
                sandy_enabled=True,
                last_synced=datetime.utcnow()
            ).on_conflict_do_update(
                index_elements=["pms_property_id"],  # or ["pmc_id", "pms_property_id"] if composite key
                set_={
                    "name": name,
                    "last_synced": datetime.utcnow(),
                    "sync_enabled": True,
                    "sandy_enabled": True
                }
            )

            session.execute(stmt)
            saved += 1

        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[DB] ❌ Failed to save properties: {e}")
    finally:
        session.close()

    return saved
  


def sync_all_pmcs():
    """Loop through all PMCs in Airtable and sync their properties."""
    total = 0
    pmcs = fetch_pmc_lookup()
    for account_id in pmcs.keys():
        print(f"[SYNC] 🔄 Syncing PMC {account_id}")
        try:
            total += sync_properties(account_id)
        except Exception as e:
            print(f"[ERROR] Failed syncing {account_id}: {e}")
    print(f"[SYNC] ✅ Total properties synced: {total}")
    return total

def ensure_pmc_structure(pmc_name: str, property_id: str, property_name: str):
    # Clean folder names for filesystem safety
    safe_pmc_name = pmc_name.replace(" ", "_")
    safe_prop_name = property_name.replace(" ", "_").replace("/", "-")
    base_dir = f"data/{safe_pmc_name}/{property_id}"

    os.makedirs(base_dir, exist_ok=True)

    # Create empty config and manual if missing
    config_path = os.path.join(base_dir, "config.json")
    manual_path = os.path.join(base_dir, "manual.txt")

    if not os.path.exists(config_path):
        with open(config_path, "w") as f:
            f.write("{}")

    if not os.path.exists(manual_path):
        with open(manual_path, "w") as f:
            f.write("")

    return base_dir




# For local test
if __name__ == "__main__":
    sync_all_pmcs()

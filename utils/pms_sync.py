import os
import requests
import re
import unicodedata

from models import PMC, PMCIntegration
from database import SessionLocal, engine
from datetime import datetime
from utils.github_sync import sync_pmc_to_github
from dotenv import load_dotenv
from utils.config import LOCAL_CLONE_PATH
from sqlalchemy import create_engine, text

# Set up PostgreSQL connection (via Render or your environment variable)
DATABASE_URL = os.getenv("DATABASE_URL")  # should be in form: postgresql://user:pass@host:port/dbname
engine = create_engine(DATABASE_URL)


load_dotenv()

def fetch_pmc_lookup():
    lookup = {}

    query = text("""
        SELECT 
            id,
            pms_account_id AS account_id,
            pms_api_key AS client_id,
            pms_api_secret AS client_secret,
            pms_integration AS pms,
            'https://api.hostaway.com/v1' AS base_url,
            'v1' AS version,
            sync_enabled
        FROM pmc
        WHERE pms_account_id IS NOT NULL
          AND pms_api_secret IS NOT NULL
          AND sync_enabled = TRUE
          AND (
            -- Hostaway: uses account_id + api_secret only
            (LOWER(pms_integration) = 'hostaway')
            OR
            -- Other PMSs: require both client_id + client_secret
            (LOWER(pms_integration) <> 'hostaway' AND pms_api_key IS NOT NULL)
          );
    """)

    with engine.connect() as conn:
        result = conn.execute(query).fetchall()

        for row in result:
            base_url = row.base_url or default_base_url(row.pms)

            lookup[str(row.account_id)] = {
                "record_id": row.id,
                "client_id": row.client_id,          # None for Hostaway (expected)
                "client_secret": row.client_secret,  # Hostaway API Key lives here
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





from datetime import datetime
from sqlalchemy import text

def save_to_postgres(properties, client_id, pmc_record_id, provider, integration_id: int):
    """
    Save property records to PostgreSQL 'properties' table.

    ✅ Upserts by UNIQUE(integration_id, external_property_id)
    ✅ Stores PMS listing/property id into pms_property_id
    ✅ Mirrors into external_property_id (for uniform lookups)
    ✅ Writes integration_id to bind properties to the specific PMS integration
    ✅ Does NOT overwrite sandy_enabled on re-sync (keeps user choices)
    """

    provider = (provider or "").strip().lower()
    if not provider:
        raise Exception("save_to_postgres: provider is required")
    if pmc_record_id is None:
        raise Exception("save_to_postgres: pmc_record_id is required")
    if integration_id is None:
        raise Exception("save_to_postgres: integration_id is required")

    def _external_id(p: dict) -> str | None:
        # Hostaway uses "id"; keep fallbacks for other PMSs
        for k in ("id", "listingId", "propertyId", "uid", "externalId"):
            v = p.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return None

    def _name(p: dict, pid: str) -> str:
        for k in ("internalListingName", "name", "title", "listingName", "propertyName"):
            v = p.get(k)
            if v and str(v).strip():
                return str(v).strip()
        return f"Property {pid}"

    insert_stmt = text("""
        INSERT INTO public.properties (
            property_name,
            pmc_id,
            integration_id,
            provider,
            pms_property_id,
            external_property_id,
            data_folder_path,
            last_synced
        ) VALUES (
            :property_name,
            :pmc_id,
            :integration_id,
            :provider,
            :pms_property_id,
            :external_property_id,
            :data_folder_path,
            :last_synced
        )
        ON CONFLICT (integration_id, external_property_id)
        DO UPDATE SET
            property_name    = EXCLUDED.property_name,
            pmc_id           = EXCLUDED.pmc_id,
            provider         = EXCLUDED.provider,
            pms_property_id  = EXCLUDED.pms_property_id,
            data_folder_path = EXCLUDED.data_folder_path,
            last_synced      = EXCLUDED.last_synced;
    """)

    now = datetime.utcnow()
    upserted = 0

    with engine.begin() as conn:
        for prop in (properties or []):
            ext_id = _external_id(prop)
            if not ext_id:
                continue

            name = _name(prop, ext_id)
            folder_property_id = f"{provider}_{ext_id}"

            folder = ensure_pmc_structure(
                pmc_name=(client_id or str(pmc_record_id)),
                property_id=folder_property_id,
                property_name=name,
            )

            conn.execute(
                insert_stmt,
                {
                    "property_name": name,
                    "pmc_id": int(pmc_record_id),
                    "integration_id": int(integration_id),
                    "provider": provider,
                    "pms_property_id": ext_id,        # Hostaway: same id
                    "external_property_id": ext_id,   # mirror
                    "data_folder_path": folder,
                    "last_synced": now,
                },
            )
            upserted += 1

    return upserted


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

''' REMOVE THIS CODE
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
'''


# Make sure these imports exist in your file:
# from models import PMC, PMCIntegration
# from database import SessionLocal
# and: default_base_url, get_access_token, fetch_properties, save_to_postgres,
# ensure_pmc_structure, sync_pmc_to_github

def sync_properties(integration_id: int) -> int:
    """
    Sync properties for a single PMS integration (source of truth: pmc_integrations).

    Steps:
    - Load integration row (provider + credentials)
    - Fetch access token + listings
    - Upsert into properties with integration_id + external_property_id
    - (Optional) sync folder skeletons to GitHub
    - Update last_synced_at on integration + pmc
    """
    if integration_id is None:
        raise Exception("integration_id is required")

    db: Session = SessionLocal()
    try:
        integ = db.query(PMCIntegration).filter(PMCIntegration.id == int(integration_id)).first()
        if not integ:
            raise Exception(f"Integration not found: id={integration_id}")

        provider = (integ.provider or "").strip().lower()
        if not provider:
            raise Exception(f"Integration id={integration_id} missing provider")

        pmc_id = integ.pmc_id
        if not pmc_id:
            raise Exception(f"Integration id={integration_id} missing pmc_id")

        account_id = (integ.account_id or "").strip()
        api_secret = (integ.api_secret or "").strip()

        if not account_id:
            raise Exception(f"Integration id={integration_id} missing account_id")
        if not api_secret:
            raise Exception(f"Integration id={integration_id} missing api_secret")

        # Base URL
        base_url = default_base_url(provider)

        # Hostaway auth model: client_id == account_id, client_secret == api_secret
        token = get_access_token(
            client_id=account_id,
            client_secret=api_secret,
            base_url=base_url,
            pms=provider,
        )

        properties = fetch_properties(token, base_url, provider) or []

        # Upsert into Postgres (IMPORTANT: uses integration_id)
        # client_id here is only used for folder naming; account_id is fine.
        save_to_postgres(
            properties=properties,
            client_id=account_id,
            pmc_record_id=int(pmc_id),
            provider=provider,
            integration_id=int(integration_id),
        )

        # Optional GitHub sync (folder key uses provider + external id)
        def _external_id(p: dict) -> str | None:
            for k in ("id", "listingId", "propertyId", "uid", "externalId"):
                v = p.get(k)
                if v is not None and str(v).strip():
                    return str(v).strip()
            return None

        def _name(p: dict) -> str:
            for k in ("internalListingName", "name", "title", "listingName", "propertyName"):
                v = p.get(k)
                if v and str(v).strip():
                    return str(v).strip()
            return "Property"

        try:
            for prop in properties:
                ext_id = _external_id(prop)
                if not ext_id:
                    continue

                name = _name(prop)
                folder_property_id = f"{provider}_{ext_id}"

                base_dir = ensure_pmc_structure(
                    pmc_name=account_id,  # folder grouping key
                    property_id=folder_property_id,
                    property_name=name,
                )

                rel_config = os.path.join("data", account_id, folder_property_id, "config.json")
                rel_manual = os.path.join("data", account_id, folder_property_id, "manual.txt")

                sync_pmc_to_github(
                    base_dir,
                    {
                        rel_config: os.path.join(base_dir, "config.json"),
                        rel_manual: os.path.join(base_dir, "manual.txt"),
                    },
                )
        except Exception as e:
            print(f"[GITHUB] ⚠️ Failed to push integration_id={integration_id} to GitHub: {e}")

        # Update timestamps
        now = datetime.utcnow()

        if hasattr(integ, "last_synced_at"):
            integ.last_synced_at = now

        pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
        if pmc and hasattr(pmc, "last_synced_at"):
            pmc.last_synced_at = now

        db.commit()

        print(
            f"[SYNC] ✅ Upserted {len(properties)} properties "
            f"for integration_id={integration_id} pmc_id={pmc_id} provider={provider}"
        )
        return len(properties)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()



def sync_properties_for_account_id(account_id: str):
    """
    Alias/wrapper for clarity. Keeps backward compatibility.
    """
    return sync_properties(account_id)

    
def save_properties_to_db(properties, client_id, pmc_id, pms):
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
                pmc_id=pmc_id,
                pms_integration=pms,
                pms_property_id=prop_id,
                property_name=name,
                sync_enabled=True,
                sandy_enabled=True,
                last_synced=datetime.utcnow()
            ).on_conflict_do_update(
                index_elements=["pms_property_id"],
                set_={
                    "property_name": name,
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



def _slugify(value: str, max_length: int = 64) -> str:
    """
    Filesystem-safe slug:
    - ASCII only
    - lowercase
    - hyphen/underscore safe
    - length capped
    """
    if not value:
        return "unknown"

    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^\w\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")

    return value[:max_length]


def ensure_pmc_structure(pmc_name: str, property_id: str, property_name: str) -> str:
    """
    Create and return the filesystem folder for a property.

    Structure:
      data/{pmc_slug}/{property_id}/
        ├── config.json
        └── manual.txt

    IMPORTANT:
    - property_id should already be provider-prefixed (e.g. hostaway_123)
    - property_name is for readability only (not used in folder path)
    """

    if not pmc_name:
        raise ValueError("ensure_pmc_structure: pmc_name is required")

    if not property_id:
        raise ValueError("ensure_pmc_structure: property_id is required")

    pmc_slug = _slugify(pmc_name)
    prop_id_safe = _slugify(property_id, max_length=128)

    base_dir = os.path.join("data", pmc_slug, prop_id_safe)
    os.makedirs(base_dir, exist_ok=True)

    config_path = os.path.join(base_dir, "config.json")
    manual_path = os.path.join(base_dir, "manual.txt")

    # Idempotent file creation
    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{}")

    if not os.path.exists(manual_path):
        with open(manual_path, "w", encoding="utf-8") as f:
            f.write("")

    return base_dir





# For local test
if __name__ == "__main__":
    sync_all_pmcs()

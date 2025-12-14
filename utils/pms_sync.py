import os
import requests
from models import PMC
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
            id,  -- ✅ This is the actual primary key used as ForeignKey in Property
            pms_account_id AS account_id,
            pms_api_key AS client_id,
            pms_api_secret AS client_secret,
            pms_integration AS pms,
            'https://api.hostaway.com/v1' AS base_url,
            'v1' AS version,
            sync_enabled
        FROM pmc
        WHERE pms_account_id IS NOT NULL
          AND pms_api_key IS NOT NULL
          AND pms_api_secret IS NOT NULL
          AND sync_enabled = TRUE;
    """)

    with engine.connect() as conn:
        result = conn.execute(query).fetchall()

        for row in result:
            base_url = row.base_url or default_base_url(row.pms)
            lookup[str(row.account_id)] = {
                "record_id": row.id,  # ✅ Now works correctly
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



def save_to_postgres(properties, client_id, pmc_record_id, provider, account_id):
    """
    Save property records to PostgreSQL 'properties' table.

    IMPORTANT:
    - Uses UNIQUE constraint:
      uq_properties_provider_external (pmc_id, provider, external_property_id)
    - provider examples: "hostaway", "lodgify", "guesty", etc.
    """

    provider = (provider or "").strip().lower()
    if not provider:
        raise Exception("save_to_postgres: provider is required")

    if pmc_record_id is None:
        raise Exception("save_to_postgres: pmc_record_id is required")

    # ---- Normalizers (cross-PMS safe) ----
    def _external_id(p: dict) -> str:
        # Prefer standard/likely keys across PMS providers
        for k in ("id", "listingId", "propertyId", "uid", "externalId"):
            v = p.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()

        # Last resort: deterministic-ish fallback (still upserts consistently for same payload)
        return str(abs(hash(str(p))))

    def _name(p: dict, ext_id: str) -> str:
        for k in ("internalListingName", "name", "title", "listingName", "propertyName"):
            v = p.get(k)
            if v and str(v).strip():
                return str(v).strip()
        return f"Property {ext_id}"

    insert_stmt = text("""
        INSERT INTO public.properties (
            property_name,
            pmc_id,
            provider,
            external_property_id,
            sandy_enabled,
            data_folder_path,
            last_synced
        ) VALUES (
            :property_name,
            :pmc_id,
            :provider,
            :external_property_id,
            :sandy_enabled,
            :data_folder_path,
            :last_synced
        )
        ON CONFLICT (pmc_id, provider, external_property_id)
        DO UPDATE SET
            property_name    = EXCLUDED.property_name,
            sandy_enabled    = EXCLUDED.sandy_enabled,
            data_folder_path = EXCLUDED.data_folder_path,
            last_synced      = EXCLUDED.last_synced;
    """)

    # NOTE: engine must exist in this module (as in your current setup)
    with engine.begin() as conn:
        for prop in (properties or []):
            ext_id = _external_id(prop)
            name = _name(prop, ext_id)

            # Folder key MUST be collision-proof across providers
            # (hostaway_123, lodgify_123, etc.)
            folder_property_id = f"{provider}_{ext_id}"

            folder = ensure_pmc_structure(
                pmc_name=(client_id or str(pmc_record_id)),
                property_id=folder_property_id,
                property_name=name,
            )

            conn.execute(insert_stmt, {
                "property_name": name,
                "pmc_id": int(pmc_record_id),  # FK to pmc.id
                "provider": provider,
                "external_property_id": ext_id,
                "sandy_enabled": True,         # default; later you’ll let them choose + bill
                "data_folder_path": folder,
                "last_synced": datetime.utcnow(),
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

def sync_properties(account_id: str) -> int:
    """
    Sync a single PMC by PMS account ID:
    - fetch PMC config from fetch_pmc_lookup()
    - fetch access token + properties from PMS
    - upsert into Postgres using (pmc_id, provider, external_property_id)
    - (optional) push config/manual skeletons to GitHub
    - update pmc.last_synced_at
    """
    pmcs = fetch_pmc_lookup() or {}
    print(f"[SYNC] fetch_pmc_lookup() keys={list(pmcs.keys())}")

    account_id_clean = (account_id or "").strip()
    if not account_id_clean:
        raise Exception("account_id is required")

    if account_id_clean not in pmcs:
        raise Exception(f"PMC not found for account ID: {account_id_clean}")

    pmc_cfg = pmcs[account_id_clean] or {}

    # Required values from your lookup
    pmc_id = pmc_cfg.get("record_id")  # this must be pmc.id (FK)
    client_id = pmc_cfg.get("client_id")  # used for folder naming in ensure_pmc_structure
    base_url = pmc_cfg.get("base_url")
    api_key = pmc_cfg.get("client_id") or pmc_cfg.get("api_key")     # legacy naming in your config
    api_secret = pmc_cfg.get("client_secret") or pmc_cfg.get("api_secret")

    provider = (pmc_cfg.get("pms") or pmc_cfg.get("provider") or "").strip().lower()

    if not pmc_id:
        raise Exception(f"PMC config for account_id={account_id_clean} missing record_id (pmc.id)")
    if not provider:
        raise Exception(f"PMC config for account_id={account_id_clean} missing provider (pms)")
    if not base_url:
        raise Exception(f"PMC config for account_id={account_id_clean} missing base_url")
    if not api_key or not api_secret:
        raise Exception(f"PMC config for account_id={account_id_clean} missing api credentials")

    # 1) Auth + fetch
    token = get_access_token(api_key, api_secret, base_url, provider)
    properties = fetch_properties(token, base_url, provider) or []

    # 2) Normalize properties for cross-PMS safety
    def _external_id(p: dict) -> str:
        # Prefer explicit id; otherwise provider-specific fallbacks
        for k in ("id", "listingId", "propertyId", "uid", "externalId"):
            v = p.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        # Last resort: stable-ish hash (still deterministic per payload)
        return str(abs(hash(str(p))) )

    def _name(p: dict) -> str:
        for k in ("internalListingName", "name", "title", "listingName", "propertyName"):
            v = p.get(k)
            if v and str(v).strip():
                return str(v).strip()
        return "Property"

    # 3) Save to PostgreSQL (your upsert should be on pmc_id+provider+external_property_id)
    # NOTE: keep your save_to_postgres signature aligned with your latest function
    save_to_postgres(
        properties,
        client_id=client_id or str(pmc_id),
        pmc_record_id=int(pmc_id),
        provider=provider,
        account_id=account_id_clean,
    )

    # 4) Optional GitHub sync (folder key uses provider+external_id to avoid collisions)
    try:
        for prop in properties:
            name = _name(prop)
            ext_id = _external_id(prop)

            # Use a stable folder id that won't collide across providers
            folder_property_id = f"{provider}_{ext_id}"

            base_dir = ensure_pmc_structure(
                pmc_name=(client_id or str(pmc_id)),
                property_id=folder_property_id,
                property_name=name,
            )

            rel_config = os.path.join("data", (client_id or str(pmc_id)), folder_property_id, "config.json")
            rel_manual = os.path.join("data", (client_id or str(pmc_id)), folder_property_id, "manual.txt")

            sync_pmc_to_github(
                base_dir,
                {
                    rel_config: os.path.join(base_dir, "config.json"),
                    rel_manual: os.path.join(base_dir, "manual.txt"),
                },
            )
    except Exception as e:
        print(f"[GITHUB] ⚠️ Failed to push PMC account_id={account_id_clean} to GitHub: {e}")

    # 5) Update last_synced_at (prefer pmc.id; fallback to pms_account_id)
    db = SessionLocal()
    try:
        db_pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
        if not db_pmc:
            db_pmc = db.query(PMC).filter(PMC.pms_account_id == account_id_clean).first()

        if db_pmc:
            db_pmc.last_synced_at = datetime.utcnow()
            db.commit()
            print(f"[SYNC] ✅ Updated last_synced_at for pmc_id={db_pmc.id} account_id={account_id_clean}")
        else:
            print(f"[SYNC] ⚠️ No PMC row found for pmc_id={pmc_id} or pms_account_id={account_id_clean}")
    except Exception as db_err:
        db.rollback()
        print(f"[DB] ❌ Failed to update last_synced_at: {db_err}")
        raise
    finally:
        db.close()

    print(f"[SYNC] ✅ Upserted {len(properties)} properties for account_id={account_id_clean} provider={provider}")
    return len(properties)





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

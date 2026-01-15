from __future__ import annotations

import os
import re
import unicodedata
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

import requests
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal, engine
from models import PMC, PMCIntegration
from utils.github_sync import sync_files_to_github
from utils.hostaway import get_listing_overview 

load_dotenv()
logger = logging.getLogger("uvicorn.error")

# Repo root on Render Disk, e.g. /data/hostscout_data
DATA_REPO_DIR = (os.getenv("DATA_REPO_DIR") or "").strip()
if not DATA_REPO_DIR:
    logger.warning("DATA_REPO_DIR is not set. PMS sync will write to local working dir unless fixed.")


# ----------------------------
# PMS base URLs
# ----------------------------
def default_base_url(provider: str) -> str:
    p = (provider or "").strip().lower()
    return {
        "hostaway": "https://api.hostaway.com/v1",
        "guesty": "https://open-api.guesty.com/v1",
        "lodgify": "https://api.lodgify.com/v1",
    }.get(p, "https://api.example.com/v1")


# ----------------------------
# Auth + fetch
# ----------------------------
def get_access_token(client_id: str, client_secret: str, base_url: str, provider: str) -> str:
    provider = (provider or "").strip().lower()

    if provider == "hostaway":
        token_url = f"{base_url}/accessTokens"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,         # Hostaway: account_id
            "client_secret": client_secret, # Hostaway: api_secret
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = requests.post(token_url, data=payload, headers=headers)

    elif provider == "guesty":
        token_url = f"{base_url}/auth"
        payload = {"clientId": client_id, "clientSecret": client_secret}
        headers = {"Content-Type": "application/json"}
        resp = requests.post(token_url, json=payload, headers=headers)

    else:
        raise Exception(f"Unsupported PMS for auth: {provider}")

    if resp.status_code != 200:
        raise Exception(f"Token request failed ({resp.status_code}): {resp.text}")

    token = (resp.json() or {}).get("access_token")
    if not token:
        raise Exception("Token response missing access_token")

    return token


def fetch_properties(access_token: str, base_url: str, provider: str) -> List[Dict]:
    provider = (provider or "").strip().lower()
    url = f"{base_url}/listings" if provider == "hostaway" else f"{base_url}/properties"

    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        raise Exception(f"Failed to fetch properties ({resp.status_code}): {resp.text}")

    data = resp.json() or {}
    if provider == "hostaway":
        return data.get("result", []) or []
    return data.get("properties", []) or []





def bootstrap_account_folders_to_github(provider: str, account_id: str, properties: List[Dict]) -> None:
    """
    Ensures folder structure + placeholder files exist for every property,
    then commits/pushes them to hostscout_data in ONE commit.
    Folder structure:
      data/{provider}_{account_id}/{provider}_{pms_property_id}/(config.json, manual.txt)
    """
    def _external_id(p: dict) -> Optional[str]:
        for k in ("id", "listingId", "propertyId", "uid", "externalId"):
            v = p.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return None

    provider = (provider or "").strip().lower()
    account_id = (account_id or "").strip()
    if not provider or not account_id:
        logger.warning("[bootstrap] missing provider/account_id; skipping")
        return

    acct_dir = f"{provider}_{_slugify(account_id, max_length=128)}"
    updated_files: Dict[str, str] = {}

    for prop in properties or []:
        ext_id = _external_id(prop)
        if not ext_id:
            continue

        # Create files inside the repo working tree (DATA_REPO_DIR)
        base_dir = ensure_pmc_structure(
            provider=provider,
            account_id=account_id,
            pms_property_id=ext_id,
        )

        prop_dir = f"{provider}_{_slugify(str(ext_id), max_length=128)}"

        rel_config = os.path.join("data", acct_dir, prop_dir, "config.json")
        rel_manual = os.path.join("data", acct_dir, prop_dir, "manual.txt")

        # Copy from the files we just ensured exist
        updated_files[rel_config] = os.path.join(base_dir, "config.json")
        updated_files[rel_manual] = os.path.join(base_dir, "manual.txt")

    if not updated_files:
        logger.info("[bootstrap] no property files to push")
        return

    sync_files_to_github(
        updated_files=updated_files,
        commit_hint=f"bootstrap {provider}_{account_id} ({len(updated_files)//2} properties)",
    )
    logger.info("[bootstrap] ✅ pushed %s properties for %s_%s", len(updated_files)//2, provider, account_id)

# ----------------------------
# Filesystem helpers
# ----------------------------
def _slugify(value: str, max_length: int = 64) -> str:
    if not value:
        return "unknown"
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^\w\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:max_length]


def _repo_root() -> Path:
    """
    Where to write the data repo on disk.
    DATA_REPO_DIR should be your repo root (contains /data and /defaults).
    """
    if DATA_REPO_DIR:
        return Path(DATA_REPO_DIR)
    # fallback: local working directory (not ideal for Render)
    return Path(".")


def ensure_pmc_structure(provider: str, account_id: str, pms_property_id: str) -> str:
    """
    Ensures folder structure in the data repo:

      {DATA_REPO_DIR}/data/{provider}_{account_id}/{provider}_{pms_property_id}/
        ├── config.json
        └── manual.txt

    Returns the *repo-relative* path to store in Postgres, e.g.:
      data/hostaway_63652/hostaway_256853
    """

    provider = (provider or "").strip().lower()
    account_id = (account_id or "").strip()
    pms_property_id = str(pms_property_id or "").strip()

    if not provider:
        raise ValueError("ensure_pmc_structure: provider is required")
    if not account_id:
        raise ValueError("ensure_pmc_structure: account_id is required")
    if not pms_property_id:
        raise ValueError("ensure_pmc_structure: pms_property_id is required")
    if not DATA_REPO_DIR:
        raise RuntimeError("DATA_REPO_DIR must be set (repo root, e.g. /data/hostscout_data)")

    acct_dir = f"{provider}_{_slugify(account_id, max_length=128)}"
    prop_dir = f"{provider}_{_slugify(pms_property_id, max_length=128)}"

    # ✅ repo-relative path (this is what goes in Postgres)
    rel_dir = os.path.join("data", acct_dir, prop_dir)

    # ✅ absolute path on disk (this is what we mkdir/write)
    abs_dir = Path(DATA_REPO_DIR) / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    cfg = abs_dir / "config.json"
    man = abs_dir / "manual.txt"

    # ✅ guarantee valid JSON (prevents JSONDecodeError)
    if not cfg.exists() or cfg.stat().st_size == 0:
        cfg.write_text("{}", encoding="utf-8")

    if not man.exists():
        man.write_text("", encoding="utf-8")

    return rel_dir



# ----------------------------
# DB upsert (integration_id-based) — now includes hero_image_url
# ----------------------------
def save_to_postgres(
    properties: List[Dict],
    client_id: str,
    pmc_record_id: int,
    provider: str,
    integration_id: int,
) -> int:
    """
    ✅ Upserts by UNIQUE(integration_id, external_property_id)
    ✅ Writes integration_id
    ✅ Does NOT overwrite sandy_enabled
    ✅ Stores data_folder_path as repo-relative: data/{provider}_{account}/{provider}_{prop}
    ✅ Stores hero_image_url (if provided)
    """
    provider = (provider or "").strip().lower()
    if not provider:
        raise ValueError("save_to_postgres: provider is required")
    if pmc_record_id is None:
        raise ValueError("save_to_postgres: pmc_record_id is required")
    if integration_id is None:
        raise ValueError("save_to_postgres: integration_id is required")
    if not client_id or not str(client_id).strip():
        raise ValueError("save_to_postgres: client_id (account_id) is required")

    def _external_id(p: dict) -> Optional[str]:
        for k in ("id", "listingId", "propertyId", "uid", "externalId"):
            v = p.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return None

    def _name(p: dict, pid: str) -> str:
        for k in ("internalListingName", "internalName", "name", "title", "listingName", "propertyName"):
            v = p.get(k)
            if v and str(v).strip():
                return str(v).strip()
        return f"Property {pid}"

    def _hero_url(p: dict) -> Optional[str]:
        # We’ll accept a few likely keys, but primarily expect "hero_image_url"
        for k in ("hero_image_url", "heroImageUrl", "hero_image", "image_url", "imageUrl"):
            v = p.get(k)
            if v and str(v).strip():
                return str(v).strip()
        return None

    stmt = text(
        """
        INSERT INTO public.properties (
            property_name,
            pmc_id,
            integration_id,
            provider,
            pms_property_id,
            external_property_id,
            data_folder_path,
            hero_image_url,
            last_synced
        ) VALUES (
            :property_name,
            :pmc_id,
            :integration_id,
            :provider,
            :pms_property_id,
            :external_property_id,
            :data_folder_path,
            :hero_image_url,
            :last_synced
        )
        ON CONFLICT (integration_id, external_property_id)
        DO UPDATE SET
            property_name    = EXCLUDED.property_name,
            pmc_id           = EXCLUDED.pmc_id,
            provider         = EXCLUDED.provider,
            pms_property_id  = EXCLUDED.pms_property_id,
            data_folder_path = EXCLUDED.data_folder_path,
            hero_image_url   = EXCLUDED.hero_image_url,
            last_synced      = EXCLUDED.last_synced;
        """
    )

    now = datetime.utcnow()
    upserted = 0

    with engine.begin() as conn:
        for prop in (properties or []):
            ext_id = _external_id(prop)
            if not ext_id:
                continue

            name = _name(prop, ext_id)
            hero_image_url = _hero_url(prop)

            # Creates folder on disk + returns repo-relative folder path
            rel_folder = ensure_pmc_structure(
                provider=provider,
                account_id=str(client_id).strip(),
                pms_property_id=ext_id,
            )

            conn.execute(
                stmt,
                {
                    "property_name": name,
                    "pmc_id": int(pmc_record_id),
                    "integration_id": int(integration_id),
                    "provider": provider,
                    "pms_property_id": ext_id,
                    "external_property_id": ext_id,
                    "data_folder_path": rel_folder,   # repo-relative
                    "hero_image_url": hero_image_url, # ✅ new
                    "last_synced": now,
                },
            )
            upserted += 1

    return upserted


# ----------------------------
# GitHub sync (optional, non-fatal)
# ----------------------------

def _try_github_sync(account_id: str, provider: str, properties: List[Dict]) -> None:
    def _external_id(p: dict) -> Optional[str]:
        for k in ("id", "listingId", "propertyId", "uid", "externalId"):
            v = p.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return None

    try:
        for prop in properties or []:
            ext_id = _external_id(prop)
            if not ext_id:
                continue

            # Creates the folder + returns the repo-relative folder path, e.g.
            # "data/hostaway_63652/hostaway_256853"
            rel_dir = ensure_pmc_structure(
                provider=provider,
                account_id=account_id,
                pms_property_id=ext_id,
            )

            # Absolute folder on disk
            abs_dir = os.path.join(DATA_REPO_DIR, rel_dir)

            rel_config = os.path.join(rel_dir, "config.json")
            rel_manual = os.path.join(rel_dir, "manual.txt")

            sync_files_to_github(
                updated_files={
                    rel_config: os.path.join(abs_dir, "config.json"),
                    rel_manual: os.path.join(abs_dir, "manual.txt"),
                },
                commit_hint=f"bootstrap {provider}_{account_id} {ext_id}",
            )

    except Exception as e:
        logger.warning("[GITHUB] ⚠️ Failed GitHub sync for account_id=%s provider=%s: %r", account_id, provider, e)




# ----------------------------
# Main sync entrypoint (cleaner + no folder creation)
# ----------------------------
def sync_properties(integration_id: int) -> int:
    """
    Sync properties for one integration (source of truth: pmc_integrations).

    What it does:
    - Fetches latest properties from PMS (e.g. Hostaway)
    - Enriches each property with hero_image_url (when supported)
    - Upserts properties into Postgres
    - Updates last_synced_at timestamps

    What it DOES NOT do:
    - Create hostscout_data folders
    - Push anything to GitHub

    (Those should be in a separate bootstrap/onboarding flow, not in sync.)
    """
    if integration_id is None:
        raise ValueError("integration_id is required")

    db: Session = SessionLocal()
    try:
        integ = (
            db.query(PMCIntegration)
            .filter(PMCIntegration.id == int(integration_id))
            .first()
        )
        if not integ:
            raise ValueError(f"Integration not found: id={integration_id}")

        provider = (integ.provider or "").strip().lower()
        if not provider:
            raise ValueError(f"Integration id={integration_id} missing provider")

        pmc_id = integ.pmc_id
        if not pmc_id:
            raise ValueError(f"Integration id={integration_id} missing pmc_id")

        account_id = (integ.account_id or "").strip()
        api_secret = (integ.api_secret or "").strip()
        if not account_id:
            raise ValueError(f"Integration id={integration_id} missing account_id")
        if not api_secret:
            raise ValueError(f"Integration id={integration_id} missing api_secret")

        base_url = default_base_url(provider)

        token = get_access_token(
            client_id=account_id,
            client_secret=api_secret,
            base_url=base_url,
            provider=provider,
        )

        # 1) Fetch properties from PMS
        props = fetch_properties(token, base_url, provider) or []

        # 2) Enrich with hero_image_url (Hostaway only)
        # NOTE: /listings does NOT include images — must call /listings/{id}?includeResources=1
        if provider == "hostaway" and props:
            for p in props:
                listing_id = p.get("id") or p.get("listingId") or p.get("listing_id")
                if not listing_id:
                    continue

                # Optional optimization: if upstream already provided one, don't refetch.
                # (Most likely it's missing, but this makes the function safe if you later cache/enrich upstream.)
                if p.get("hero_image_url"):
                    continue

                try:
                    hero_url, _, _ = get_listing_overview(
                        listing_id=str(listing_id),
                        client_id=account_id,
                        client_secret=api_secret,
                    )
                    p["hero_image_url"] = hero_url or None
                except Exception as e:
                    # non-fatal: keep syncing even if one listing fails
                    logger.warning(
                        "[SYNC] ⚠️ hero_image_url lookup failed for listing_id=%s: %r",
                        listing_id,
                        e,
                    )
                    p["hero_image_url"] = None

        # 3) Upsert into Postgres (make sure save_to_postgres reads p["hero_image_url"])
        upserted = save_to_postgres(
            properties=props,
            client_id=account_id,
            pmc_record_id=int(pmc_id),
            provider=provider,
            integration_id=int(integration_id),
        )

        # 4) Update last_synced_at timestamps
        now = datetime.utcnow()
        if hasattr(integ, "last_synced_at"):
            integ.last_synced_at = now

        pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
        if pmc and hasattr(pmc, "last_synced_at"):
            pmc.last_synced_at = now

        db.commit()

        logger.info(
            "[SYNC] ✅ Upserted %s properties for integration_id=%s provider=%s",
            upserted,
            integration_id,
            provider,
        )
        return upserted

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def sync_all_integrations() -> int:
    """
    Sync all connected integrations (useful for cron jobs).
    """
    db: Session = SessionLocal()
    try:
        ids = [
            row[0]
            for row in db.query(PMCIntegration.id)
                         .filter(PMCIntegration.is_connected.is_(True))
                         .order_by(PMCIntegration.id.asc())
                         .all()
        ]
    finally:
        db.close()

    total = 0
    for iid in ids:
        try:
            total += sync_properties(iid)
        except Exception as e:
            logger.warning("[SYNC] ❌ integration_id=%s failed: %r", iid, e)

    logger.info("[SYNC] ✅ Total properties synced: %s", total)
    return total


if __name__ == "__main__":
    sync_all_integrations()

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from typing import Optional, List, Dict

import requests
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal, engine
from models import PMC, PMCIntegration
from utils.github_sync import sync_files_to_github


load_dotenv()


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


def ensure_pmc_structure(provider: str, account_id: str, pms_property_id: str) -> str:
    """
    Creates / ensures folder structure in the *data repo*:

    data/
      {provider}_{account_id}/
        {provider}_{pms_property_id}/
          config.json
          manual.txt
    """
    provider = (provider or "").strip().lower()
    if not provider:
        raise ValueError("ensure_pmc_structure: provider is required")
    if not account_id:
        raise ValueError("ensure_pmc_structure: account_id is required")
    if not pms_property_id:
        raise ValueError("ensure_pmc_structure: pms_property_id is required")

    acct_dir = f"{provider}_{_slugify(account_id, max_length=128)}"
    prop_dir = f"{provider}_{_slugify(str(pms_property_id), max_length=128)}"

    base_dir = os.path.join("data", acct_dir, prop_dir)
    os.makedirs(base_dir, exist_ok=True)

    config_path = os.path.join(base_dir, "config.json")
    manual_path = os.path.join(base_dir, "manual.txt")

    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{}")

    if not os.path.exists(manual_path):
        with open(manual_path, "w", encoding="utf-8") as f:
            f.write("")

    return base_dir



# ----------------------------
# DB upsert (integration_id-based)
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
    ✅ Writes integration_id (ties property to a specific integration)
    ✅ Does NOT overwrite sandy_enabled
    """
    provider = (provider or "").strip().lower()
    if not provider:
        raise Exception("save_to_postgres: provider is required")
    if pmc_record_id is None:
        raise Exception("save_to_postgres: pmc_record_id is required")
    if integration_id is None:
        raise Exception("save_to_postgres: integration_id is required")

    def _external_id(p: dict) -> Optional[str]:
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

    stmt = text("""
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
           folder = ensure_pmc_structure(
                provider=provider,
                account_id=client_id,
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
                    "data_folder_path": folder,
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

            base_dir = ensure_pmc_structure(
                provider=provider,
                account_id=account_id,
                pms_property_id=ext_id,
            )

            acct_dir = f"{provider}_{_slugify(account_id, max_length=128)}"
            prop_dir = f"{provider}_{_slugify(str(ext_id), max_length=128)}"

            rel_config = os.path.join("data", acct_dir, prop_dir, "config.json")
            rel_manual = os.path.join("data", acct_dir, prop_dir, "manual.txt")

            sync_files_to_github(
                updated_files={
                    rel_config: os.path.join(base_dir, "config.json"),
                    rel_manual: os.path.join(base_dir, "manual.txt"),
                },
                commit_hint=f"sync {provider} {account_id} {ext_id}",
            )

    except Exception as e:
        print(f"[GITHUB] ⚠️ Failed GitHub sync for account_id={account_id} provider={provider}: {e}")


# ----------------------------
# Main sync entrypoint
# ----------------------------
def sync_properties(integration_id: int) -> int:
    """
    Sync properties for one integration (source of truth: pmc_integrations).
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

        base_url = default_base_url(provider)

        token = get_access_token(
            client_id=account_id,
            client_secret=api_secret,
            base_url=base_url,
            provider=provider,
        )

        props = fetch_properties(token, base_url, provider) or []

        save_to_postgres(
            properties=props,
            client_id=account_id,
            pmc_record_id=int(pmc_id),
            provider=provider,
            integration_id=int(integration_id),
        )

        _try_github_sync(account_id=account_id, provider=provider, properties=props)

        now = datetime.utcnow()
        if hasattr(integ, "last_synced_at"):
            integ.last_synced_at = now

        pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
        if pmc and hasattr(pmc, "last_synced_at"):
            pmc.last_synced_at = now

        db.commit()

        print(f"[SYNC] ✅ Upserted {len(props)} properties for integration_id={integration_id} provider={provider}")
        return len(props)

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
            print(f"[SYNC] ❌ integration_id={iid} failed: {e}")

    print(f"[SYNC] ✅ Total properties synced: {total}")
    return total


if __name__ == "__main__":
    sync_all_integrations()

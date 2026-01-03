import os
import json
import requests
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

LOCAL_CLONE_PATH = os.getenv("LOCAL_CLONE_PATH", "/tmp/hostscout-data")

# Put your default config here (adjust path to wherever you store it)
DEFAULT_CONFIG_PATH = Path("data/default/config.json")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge dictionaries: override wins; nested dicts merged."""
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=1)
def load_default_config() -> Dict[str, Any]:
    """
    Load default config.json (global fallback).
    """
    return _read_json_file(DEFAULT_CONFIG_PATH)


def _fetch_property_from_airtable(slug: str) -> Optional[Dict[str, Any]]:
    """
    Fetch config-like fields from Airtable for a given property slug.
    Returns a dict or None.
    """
    base_id = os.getenv("AIRTABLE_BASE_ID")
    api_key = os.getenv("AIRTABLE_API_KEY")
    table_name = "Properties"

    if not base_id or not api_key:
        return None

    url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"filterByFormula": f"LOWER(property_slug) = '{slug.lower()}'"}

    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()

    records = response.json().get("records", [])
    if not records:
        return None

    fields = records[0].get("fields", {}) or {}

    # IMPORTANT:
    # Airtable currently only provides a subset (not full JSON).
    # We'll map known fields, and let defaults fill everything else.
    cfg: Dict[str, Any] = {
        "listing_id": str(fields.get("listing_id")) if fields.get("listing_id") is not None else None,
        "property_name": fields.get("property_name"),
        "emergency_phone": fields.get("emergency_phone", ""),
        "default_checkin_time": int(fields.get("default_checkin_time", 16)),
        "default_checkout_time": int(fields.get("default_checkout_time", 10)),
    }

    # OPTIONAL: if you add an Airtable field called `assistant_json` containing JSON text,
    # we can parse it and merge it into cfg["assistant"] automatically.
    assistant_json = fields.get("assistant_json")
    if isinstance(assistant_json, str) and assistant_json.strip():
        try:
            cfg["assistant"] = json.loads(assistant_json)
        except Exception:
            # ignore bad json
            pass

    return cfg


def _load_property_local_config(slug: str) -> Dict[str, Any]:
    """
    Fallback local JSON config.
    Looks for: data/{slug}/config.json
    """
    path = Path(f"data/{slug}/config.json")
    if not path.exists():
        raise FileNotFoundError(f"No local config at {path}")

    config = _read_json_file(path)
    return config or {}


def _normalize_required_keys(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure legacy keys exist and are the correct types.
    This protects the rest of your app from missing keys.
    """
    out = dict(cfg or {})

    # legacy keys
    out["listing_id"] = str(out.get("listing_id")) if out.get("listing_id") is not None else ""
    out["property_name"] = out.get("property_name") or ""
    out["emergency_phone"] = out.get("emergency_phone", "") or ""

    try:
        out["default_checkin_time"] = int(out.get("default_checkin_time", 16))
    except Exception:
        out["default_checkin_time"] = 16

    try:
        out["default_checkout_time"] = int(out.get("default_checkout_time", 10))
    except Exception:
        out["default_checkout_time"] = 10

    # assistant/personality always a dict
    if not isinstance(out.get("assistant"), dict):
        out["assistant"] = {}

    # helpful defaults inside assistant
    out["assistant"].setdefault("name", "Sandy")
    out["assistant"].setdefault("avatar_url", "/static/img/sandy.png")
    out["assistant"].setdefault("voice", {})
    out["assistant"].setdefault("quick_replies", ["WiFi", "Door code", "Parking", "Check-out time"])

    return out


@lru_cache(maxsize=128)
def load_property_config(slug: str) -> Dict[str, Any]:
    """
    Load property config:
    - Start with default config
    - Merge in Airtable (if available)
    - Merge in local config.json (if available) as strongest override
    - Normalize required keys
    """
    default_cfg = load_default_config()

    airtable_cfg: Dict[str, Any] = {}
    try:
        maybe = _fetch_property_from_airtable(slug)
        if maybe:
            airtable_cfg = maybe
    except Exception as e:
        print(f"[Config] Airtable fetch failed for {slug}: {e}")

    local_cfg: Dict[str, Any] = {}
    try:
        local_cfg = _load_property_local_config(slug)
    except Exception as e:
        # If local config missing, that's okay if Airtable worked
        # but if both missing, we'll raise below.
        local_cfg = {}
        local_err = e

    # If we got nothing from Airtable AND nothing local, error
    if not airtable_cfg and not local_cfg:
        raise ValueError(f"[Config] Failed to load config for {slug}: {local_err}")

    # Merge order: default -> airtable -> local (local strongest)
    merged = deep_merge(default_cfg, airtable_cfg)
    merged = deep_merge(merged, local_cfg)

    return _normalize_required_keys(merged)

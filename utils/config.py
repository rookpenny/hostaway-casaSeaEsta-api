import json
import os

def load_property_config(slug: str) -> dict:
    config_path = f"data/{slug}/config.json"
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"No config for property: {slug}")
    with open(config_path, "r") as f:
        return json.load(f)

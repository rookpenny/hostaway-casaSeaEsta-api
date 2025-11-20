import requests

GITHUB_OWNER = "stayhub"
GITHUB_REPO = "stayhub-data"
GITHUB_BRANCH = "main"

def fetch_github_file(pmc_name: str, property_name: str, filename: str) -> str:
    """Fetches a file (manual.txt or config.json) from the GitHub stayhub-data repo."""
    base_url = "https://raw.githubusercontent.com"
    path = f"{base_url}/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/data/{pmc_name}/{property_name}/{filename}"

    response = requests.get(path)
    if response.status_code == 200:
        return response.text
    else:
        raise Exception(f"Failed to fetch file from GitHub: {path} ({response.status_code})")

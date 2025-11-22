import os
import shutil
from git import Repo
from datetime import datetime

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "stayhub/stayhub-data"
BRANCH = "main"
LOCAL_CLONE_PATH = "/tmp/stayhub-data"
COMMIT_AUTHOR = os.getenv("COMMIT_AUTHOR", "PMS Sync Bot")
COMMIT_EMAIL = os.getenv("COMMIT_EMAIL", "syncbot@stayhub.io")

def github_url_with_token():
    if not GITHUB_TOKEN:
        raise EnvironmentError("GITHUB_TOKEN not set in environment")
    return f"https://{GITHUB_TOKEN}:x-oauth-basic@github.com/{GITHUB_REPO}.git"

def clone_repo():
    if os.path.exists(LOCAL_CLONE_PATH):
        shutil.rmtree(LOCAL_CLONE_PATH)
    return Repo.clone_from(github_url_with_token(), LOCAL_CLONE_PATH, branch=BRANCH)

def sync_pmc_to_github(pms_client_id: str, pms_property_id: str, updated_files: dict):
    repo = clone_repo()

    dest_path = os.path.join(LOCAL_CLONE_PATH, "data", f"pmc_{pms_client_id}", f"property_{pms_property_id}")
    os.makedirs(dest_path, exist_ok=True)

    for filename, local_source_path in updated_files.items():
        print(f"[GITHUB] Copying {filename} to {dest_path}")
        shutil.copy(local_source_path, os.path.join(dest_path, filename))

    repo.git.add(A=True)
    if repo.is_dirty():
        commit_message = f"Sync property {pms_property_id} under PMC {pms_client_id} @ {datetime.utcnow().isoformat()}"
        repo.index.commit(commit_message, author=repo.config_writer().config.get_value("user", "name", COMMIT_AUTHOR))
        repo.remote(name='origin').push()
        print(f"[GITHUB] ✅ Changes pushed for PMC {pms_client_id}, Property {pms_property_id}")
    else:
        print(f"[GITHUB] No changes to push for PMC {pms_client_id}, Property {pms_property_id}")

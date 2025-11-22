import os
import shutil
from git import Repo
from datetime import datetime

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "rookpenny/hostscout_data"  # ✅ Updated repo
BRANCH = "main"
LOCAL_CLONE_PATH = "/tmp/hostscout-data"  # ✅ Updated path
COMMIT_AUTHOR = os.getenv("COMMIT_AUTHOR", "PMS Sync Bot")
COMMIT_EMAIL = os.getenv("COMMIT_EMAIL", "syncbot@hostscout.io")

def github_url_with_token():
    if not GITHUB_TOKEN:
        raise EnvironmentError("GITHUB_TOKEN not set in environment")
    return f"https://{GITHUB_TOKEN}:x-oauth-basic@github.com/{GITHUB_REPO}.git"

def clone_repo():
    if os.path.exists(LOCAL_CLONE_PATH):
        shutil.rmtree(LOCAL_CLONE_PATH)
    return Repo.clone_from(github_url_with_token(), LOCAL_CLONE_PATH, branch=BRANCH)

def sync_pmc_to_github(dest_folder_path: str, updated_files: dict):
    repo = clone_repo()

    full_path = os.path.join(LOCAL_CLONE_PATH, dest_folder_path.lstrip("/"))
    os.makedirs(full_path, exist_ok=True)

    for filename, local_source_path in updated_files.items():
        print(f"[GITHUB] Copying {filename} to {full_path}")
        shutil.copy(local_source_path, os.path.join(full_path, filename))

    repo.git.add(A=True)
    if repo.is_dirty():
        commit_message = f"Sync update to {dest_folder_path} @ {datetime.utcnow().isoformat()}"
        repo.index.commit(commit_message, author=repo.config_writer().config.get_value("user", "name", COMMIT_AUTHOR))
        repo.remote(name='origin').push()
        print(f"[GITHUB] ✅ Changes pushed to {dest_folder_path}")
    else:
        print(f"[GITHUB] No changes to push for {dest_folder_path}")

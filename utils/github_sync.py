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

def sync_pmc_to_github(pmc_name: str):
    repo = clone_repo()
    source_path = os.path.join("data", pmc_name)
    dest_path = os.path.join(LOCAL_CLONE_PATH, "data", pmc_name)

    print(f"[GITHUB] Copying files from {source_path} to {dest_path}")
    if os.path.exists(dest_path):
        shutil.rmtree(dest_path)
    shutil.copytree(source_path, dest_path)

    repo.git.add(A=True)
    if repo.is_dirty():
        commit_message = f"Sync properties for PMC {pmc_name} @ {datetime.utcnow().isoformat()}"
        repo.index.commit(commit_message, author=repo.config_writer().config.get_value("user", "name", COMMIT_AUTHOR))
        origin = repo.remote(name='origin')
        origin.push()
        print(f"[GITHUB] ✅ Changes pushed for PMC {pmc_name}")
    else:
        print(f"[GITHUB] No changes to push for PMC {pmc_name}")

# For local testing
if __name__ == "__main__":
    sync_pmc_to_github("coastal_villas")

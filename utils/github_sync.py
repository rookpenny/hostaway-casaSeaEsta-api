import os
import shutil
from git import Repo, Actor  # Add this import at the top
from datetime import datetime

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "rookpenny/hostscout_data"
BRANCH = "main"
LOCAL_CLONE_PATH = os.getenv("DATA_REPO_DIR", "/var/data/hostscout_data")
COMMIT_AUTHOR = os.getenv("COMMIT_AUTHOR", "PMS Sync Bot")
COMMIT_EMAIL = os.getenv("COMMIT_EMAIL", "syncbot@hostscout.ai")

def github_url_with_token():
    if not GITHUB_TOKEN:
        raise EnvironmentError("GITHUB_TOKEN not set in environment")
    return f"https://{GITHUB_TOKEN}:x-oauth-basic@github.com/{GITHUB_REPO}.git"

def clone_repo():
    if os.path.exists(LOCAL_CLONE_PATH):
        shutil.rmtree(LOCAL_CLONE_PATH)
    return Repo.clone_from(github_url_with_token(), LOCAL_CLONE_PATH, branch=BRANCH)

def ensure_repo():
    if os.path.exists(os.path.join(LOCAL_CLONE_PATH, ".git")):
        repo = Repo(LOCAL_CLONE_PATH)
        repo.git.fetch("--all")
        repo.git.reset("--hard", f"origin/{BRANCH}")
        return repo

    os.makedirs(LOCAL_CLONE_PATH, exist_ok=True)
    return Repo.clone_from(
        github_url_with_token(),
        LOCAL_CLONE_PATH,
        branch=BRANCH,
    )

def sync_pmc_to_github(dest_folder_path: str, updated_files: dict):
   
    repo = ensure_repo()

    for rel_path, local_source_path in updated_files.items():
        full_path = os.path.join(LOCAL_CLONE_PATH, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        print(f"[GITHUB] Copying {rel_path} to {full_path}")
        shutil.copy(local_source_path, full_path)

    repo.git.add(A=True)

    if repo.is_dirty():
        commit_message = f"Sync update to {dest_folder_path} @ {datetime.utcnow().isoformat()}"
        author = Actor(COMMIT_AUTHOR, COMMIT_EMAIL)
        repo.index.commit(commit_message, author=author)
        repo.remote(name="origin").push()
        print(f"[GITHUB] ✅ Changes pushed to {dest_folder_path}")
    else:
        print(f"[GITHUB] No changes to push for {dest_folder_path}")

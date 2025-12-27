import os
import shutil
import logging
from pathlib import Path
from git import Repo, Actor  # Add this import at the top
from datetime import datetime

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "rookpenny/hostscout_data"
BRANCH = "main"
LOCAL_CLONE_PATH = os.getenv("DATA_REPO_DIR", "/var/data/hostscout_data")
COMMIT_AUTHOR = os.getenv("COMMIT_AUTHOR", "PMS Sync Bot")
COMMIT_EMAIL = os.getenv("COMMIT_EMAIL", "syncbot@hostscout.ai")


def clone_repo():
    if os.path.exists(LOCAL_CLONE_PATH):
        shutil.rmtree(LOCAL_CLONE_PATH)
    return Repo.clone_from(github_url_with_token(), LOCAL_CLONE_PATH, branch=BRANCH)

import os
import shutil
import logging
from pathlib import Path
from git import Repo

logger = logging.getLogger("uvicorn.error")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "rookpenny/hostscout_data")
BRANCH = os.getenv("GITHUB_BRANCH", "main")

# This should point to the *repo root* on the Render Disk, e.g. /var/data/hostscout_data
DATA_REPO_DIR = (os.getenv("DATA_REPO_DIR") or "").strip()

def github_url_with_token() -> str:
    if not GITHUB_TOKEN:
        raise EnvironmentError("GITHUB_TOKEN not set in environment")
    return f"https://{GITHUB_TOKEN}:x-oauth-basic@github.com/{GITHUB_REPO}.git"

def _pick_clone_dir() -> Path:
    """
    Prefer Render Disk path if writable, otherwise fall back to /tmp
    so the app doesn't crash on boot.
    """
    if DATA_REPO_DIR:
        p = Path(DATA_REPO_DIR)
        # Only create the repo dir itself. (Do NOT try to create /var/data.)
        try:
            p.mkdir(parents=False, exist_ok=True)
            # quick writable check
            testfile = p / ".write_test"
            testfile.write_text("ok", encoding="utf-8")
            testfile.unlink(missing_ok=True)
            return p
        except Exception as e:
            logger.warning("DATA_REPO_DIR not usable (%s): %r. Falling back to /tmp.", DATA_REPO_DIR, e)

    return Path("/tmp/hostscout_data")

def ensure_repo() -> str:
    """
    Ensures the data repo exists locally. Returns the local path.
    """
    clone_dir = _pick_clone_dir()

    # If it already looks like a git repo, just fetch/pull
    git_dir = clone_dir / ".git"
    if git_dir.exists():
        try:
            repo = Repo(str(clone_dir))
            repo.remote().fetch()
            repo.git.checkout(BRANCH)
            repo.remote().pull()
            logger.info("Data repo updated at %s", clone_dir)
            return str(clone_dir)
        except Exception as e:
            logger.warning("Repo exists but update failed (%s). Re-cloning. Err=%r", clone_dir, e)
            shutil.rmtree(clone_dir, ignore_errors=True)

    # Fresh clone
    clone_dir.mkdir(parents=True, exist_ok=True)
    Repo.clone_from(github_url_with_token(), str(clone_dir), branch=BRANCH)
    logger.info("Data repo cloned to %s", clone_dir)
    return str(clone_dir)


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

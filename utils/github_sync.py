import os
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple

from git import Repo, Actor

logger = logging.getLogger("uvicorn.error")

# Env
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()
GITHUB_REPO = (os.getenv("GITHUB_REPO") or "rookpenny/hostscout_data").strip()
BRANCH = (os.getenv("GITHUB_BRANCH") or "main").strip()

# This should be the *repo root* on your Render Disk mount, e.g. /var/data/hostscout_data
DATA_REPO_DIR = (os.getenv("DATA_REPO_DIR") or "").strip()

COMMIT_AUTHOR = (os.getenv("COMMIT_AUTHOR") or "PMS Sync Bot").strip()
COMMIT_EMAIL = (os.getenv("COMMIT_EMAIL") or "syncbot@hostscout.ai").strip()


def github_url_with_token() -> str:
    if not GITHUB_TOKEN:
        raise EnvironmentError("GITHUB_TOKEN not set in environment")
    return f"https://{GITHUB_TOKEN}:x-oauth-basic@github.com/{GITHUB_REPO}.git"


def _pick_clone_dir() -> Path:
    """
    Prefer Render Disk path if usable, otherwise fall back to /tmp so we don't crash boot.
    DATA_REPO_DIR should be the repo root (the folder that will contain .git).
    """
    if DATA_REPO_DIR:
        p = Path(DATA_REPO_DIR)
        try:
            p.mkdir(parents=True, exist_ok=True)
            testfile = p / ".write_test"
            testfile.write_text("ok", encoding="utf-8")
            testfile.unlink(missing_ok=True)
            return p
        except Exception as e:
            logger.warning("DATA_REPO_DIR not usable (%s): %r. Falling back to /tmp.", DATA_REPO_DIR, e)

    return Path("/tmp/hostscout_data")


def ensure_repo() -> Tuple[Repo, Path]:
    """
    Ensures the hostscout_data repo exists locally and is up to date.
    Returns (Repo, repo_path).
    """
    clone_dir = _pick_clone_dir()
    git_dir = clone_dir / ".git"

    # If already a git repo, fetch/pull
    if git_dir.exists():
        try:
            repo = Repo(str(clone_dir))
            repo.remote().fetch()
            repo.git.checkout(BRANCH)
            repo.remote().pull()
            logger.info("✅ Data repo updated at %s", clone_dir)
            return repo, clone_dir
        except Exception as e:
            logger.warning("Repo exists but update failed (%s). Re-cloning. Err=%r", clone_dir, e)
            shutil.rmtree(clone_dir, ignore_errors=True)

    # Fresh clone
    clone_dir.mkdir(parents=True, exist_ok=True)
    repo = Repo.clone_from(github_url_with_token(), str(clone_dir), branch=BRANCH)
    logger.info("✅ Data repo cloned to %s", clone_dir)
    return repo, clone_dir


def sync_files_to_github(updated_files: Dict[str, str], commit_hint: str = "") -> None:
    """
    updated_files: mapping of repo-relative path -> local_source_path
      example:
        {
          "data/hostaway_63652/config.json": "/tmp/config.json",
          "data/hostaway_63652/manual.txt": "/tmp/manual.txt",
        }
    """
    repo, repo_root = ensure_repo()

    # Copy files into repo working tree
    for rel_path, local_source_path in updated_files.items():
        dest = repo_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local_source_path, str(dest))
        logger.info("[GITHUB] Copied %s -> %s", local_source_path, dest)

    repo.git.add(A=True)

    if repo.is_dirty():
        msg = commit_hint.strip() or f"Sync update @ {datetime.utcnow().isoformat()}Z"
        author = Actor(COMMIT_AUTHOR, COMMIT_EMAIL)
        repo.index.commit(msg, author=author)
        repo.remote(name="origin").push()
        logger.info("✅ Pushed changes to %s (%s)", GITHUB_REPO, BRANCH)
    else:
        logger.info("[GITHUB] No changes to push")

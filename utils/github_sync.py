import os
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple

from git import Repo, Actor, InvalidGitRepositoryError, NoSuchPathError, GitCommandError

logger = logging.getLogger("uvicorn.error")

# Env
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()
GITHUB_REPO = (os.getenv("GITHUB_REPO") or "rookpenny/hostscout_data").strip()
BRANCH = (os.getenv("GITHUB_BRANCH") or "main").strip()

# Repo root on Render Disk, e.g. /data/hostscout_data
DATA_REPO_DIR = (os.getenv("DATA_REPO_DIR") or "").strip()

COMMIT_AUTHOR = (os.getenv("COMMIT_AUTHOR") or "PMS Sync Bot").strip()
COMMIT_EMAIL = (os.getenv("COMMIT_EMAIL") or "syncbot@hostscout.ai").strip()


def github_url_with_token() -> str:
    if not GITHUB_TOKEN:
        raise EnvironmentError("GITHUB_TOKEN not set in environment")
    # Do NOT log this URL anywhere (contains token)
    return f"https://{GITHUB_TOKEN}:x-oauth-basic@github.com/{GITHUB_REPO}.git"


def _pick_repo_dir() -> Path:
    """
    Prefer Render Disk path if usable, otherwise fall back to /tmp.
    DATA_REPO_DIR should be the repo root (folder that will contain .git).
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
    repo_dir = _pick_repo_dir()

    # If directory exists but is NOT a git repo, wipe it and re-clone
    try:
        repo = Repo(str(repo_dir))
        # valid repo
    except (InvalidGitRepositoryError, NoSuchPathError):
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        repo_dir.mkdir(parents=True, exist_ok=True)
        repo = Repo.clone_from(github_url_with_token(), str(repo_dir), branch=BRANCH)
        logger.info("✅ Data repo cloned to %s", repo_dir)
        return repo, repo_dir

    # Repo exists: checkout + pull
    try:
        origin = repo.remote(name="origin")

        # Ensure correct branch exists locally
        try:
            repo.git.checkout(BRANCH)
        except GitCommandError:
            # Branch might not exist locally yet
            origin.fetch()
            repo.git.checkout("-b", BRANCH, f"origin/{BRANCH}")

        origin.fetch()
        origin.pull()
        logger.info("✅ Data repo updated at %s", repo_dir)
        return repo, repo_dir

    except Exception as e:
        logger.warning("Repo update failed (%s). Re-cloning. Err=%r", repo_dir, e)
        shutil.rmtree(repo_dir, ignore_errors=True)
        repo_dir.mkdir(parents=True, exist_ok=True)
        repo = Repo.clone_from(github_url_with_token(), str(repo_dir), branch=BRANCH)
        logger.info("✅ Data repo re-cloned to %s", repo_dir)
        return repo, repo_dir


def sync_files_to_github(updated_files: Dict[str, str], commit_hint: str = "") -> None:
    """
    updated_files: mapping of repo-relative path -> local_source_path
    Example:
      {
        "data/hostaway_63652/hostaway_256853/config.json": "/tmp/config.json",
        "data/hostaway_63652/hostaway_256853/manual.txt": "/tmp/manual.txt",
      }
    """
    repo, repo_root = ensure_repo()

    # Copy files into repo working tree
    for rel_path, local_source_path in (updated_files or {}).items():
        if not rel_path or not local_source_path:
            continue

        src = Path(local_source_path)
        if not src.exists():
            logger.warning("[GITHUB] Source file missing: %s", src)
            continue

        dest = repo_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(src), str(dest))
        logger.info("[GITHUB] Copied %s -> %s", src, dest)

    repo.git.add(A=True)

    if repo.is_dirty(untracked_files=True):
        msg = commit_hint.strip() or f"Sync update @ {datetime.utcnow().isoformat()}Z"
        author = Actor(COMMIT_AUTHOR, COMMIT_EMAIL)
        repo.index.commit(msg, author=author)
        repo.remote(name="origin").push()
        logger.info("✅ Pushed changes to %s (%s)", GITHUB_REPO, BRANCH)
    else:
        logger.info("[GITHUB] No changes to push")

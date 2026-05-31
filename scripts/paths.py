"""Filesystem paths used across the pipeline."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = REPO_ROOT / "data"
MASTER_DB = DATA_DIR / "master.db"
RAW_DIR = DATA_DIR / "raw"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
DONATIONS_DIR = DATA_DIR / "donations"

# Phase 3 — MLB-relevant legislation / votes / legislators.
# A SEPARATE DB from master.db, deliberately NOT tracked via Git LFS (see
# .gitattributes — only data/master.db is LFS). It is small (a curated bill set
# + roll-call votes only on those bills + the legislator crosswalk = a few MB)
# and committed as a normal git blob so its diffs stay readable and a commit
# does not re-push master.db's ~124 MB LFS object. The donation data in
# master.db is the read-only join target; legislation.db is ATTACHed at query
# time (CHARTER.md §Phase 3, GOVERNANCE.md §6).
LEGISLATION_DB = DATA_DIR / "legislation.db"
LEGISLATION_DIR = REPO_ROOT / "legislation"
LEGISLATION_BILLS_DIR = LEGISLATION_DIR / "bills"
# Raw Congress.gov / Clerk / Senate / crosswalk payloads, persisted before
# parsing (GOVERNANCE.md §1.4). Lives under the gitignored data/raw/ tree.
LEGISLATION_RAW_DIR = RAW_DIR / "legislation"

OWNERS_DIR = REPO_ROOT / "owners"
CATALOG_DIR = REPO_ROOT / "catalog"
PROVENANCE_LOG = CATALOG_DIR / "PROVENANCE_LOG.md"
REVIEW_QUEUE_MD = CATALOG_DIR / "REVIEW_QUEUE.md"

REPORTS_DIR = REPO_ROOT / "reports"
REPORTS_DATA_DIR = REPORTS_DIR / "data"
REVIEWS_DIR = REPO_ROOT / "reviews"


def ensure_data_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, SNAPSHOTS_DIR, DONATIONS_DIR, DONATIONS_DIR / "_aggregate"):
        d.mkdir(parents=True, exist_ok=True)


def legislation_raw_dir() -> Path:
    """data/raw/legislation/ — created on demand. Gitignored (under data/raw/)."""
    LEGISLATION_RAW_DIR.mkdir(parents=True, exist_ok=True)
    return LEGISLATION_RAW_DIR


def raw_dir_for(slug: str) -> Path:
    p = RAW_DIR / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def donations_dir_for(slug: str) -> Path:
    p = DONATIONS_DIR / slug
    (p / "by_cycle").mkdir(parents=True, exist_ok=True)
    return p


def relpath(p: Path) -> str:
    """Return path relative to repo root, POSIX-style for portability."""
    return p.resolve().relative_to(REPO_ROOT).as_posix()

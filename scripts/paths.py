"""Filesystem paths used across the pipeline."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = REPO_ROOT / "data"
MASTER_DB = DATA_DIR / "master.db"
RAW_DIR = DATA_DIR / "raw"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
DONATIONS_DIR = DATA_DIR / "donations"

OWNERS_DIR = REPO_ROOT / "owners"
CATALOG_DIR = REPO_ROOT / "catalog"
PROVENANCE_LOG = CATALOG_DIR / "PROVENANCE_LOG.md"
REVIEW_QUEUE_MD = CATALOG_DIR / "REVIEW_QUEUE.md"

REPORTS_DIR = REPO_ROOT / "reports"
REVIEWS_DIR = REPO_ROOT / "reviews"


def ensure_data_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, SNAPSHOTS_DIR, DONATIONS_DIR, DONATIONS_DIR / "_aggregate"):
        d.mkdir(parents=True, exist_ok=True)


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

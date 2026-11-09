"""Tests for scripts/merge_append_log.py — the append-only merge driver for
catalog/PROVENANCE_LOG.md that keeps concurrent workflow appends from failing the
`git pull --rebase` before push (2026-11-09 IL backfill incident)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.merge_append_log import merge_append_only

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER = REPO_ROOT / "scripts" / "merge_append_log.py"

BASE = b"# PROVENANCE_LOG\n\nPreamble.\n\n### 2026-11-03 \xe2\x80\x94 INGESTION (bills)\n\n- **source**: `congress.gov`\n"
FED = b"\n### 2026-11-08 \xe2\x80\x94 INGESTION\n\n- **run_id**: `aaaa`\n- **source**: `FEC`\n"
IL = b"\n### 2026-11-09 \xe2\x80\x94 STATE_INGESTION\n\n- **run_id**: `bbbb`\n- **source**: `ISBE`\n"


# ── pure function ────────────────────────────────────────────────────────────

def test_both_sides_append_keeps_both_tails_in_order():
    assert merge_append_only(BASE, BASE + FED, BASE + IL) == BASE + FED + IL


def test_one_side_unchanged_takes_the_other():
    assert merge_append_only(BASE, BASE, BASE + IL) == BASE + IL
    assert merge_append_only(BASE, BASE + FED, BASE) == BASE + FED


def test_identical_appends_are_not_duplicated():
    assert merge_append_only(BASE, BASE + FED, BASE + FED) == BASE + FED


def test_rotation_on_either_side_is_refused():
    rotated = b"# PROVENANCE_LOG\n\nPreamble.\n" + FED  # head rewritten
    assert merge_append_only(BASE, rotated, BASE + IL) is None
    assert merge_append_only(BASE, BASE + FED, rotated) is None


def test_no_common_ancestor_is_refused():
    # add/add (e.g. a shallow checkout with no merge base) arrives with an empty
    # base; concatenating would duplicate the entire log.
    assert merge_append_only(b"", BASE + FED, BASE + IL) is None


def test_edit_inside_base_is_refused():
    edited = BASE.replace(b"congress.gov", b"congress.gov (amended)") + IL
    assert merge_append_only(BASE, BASE + FED, edited) is None


# ── real git rebase, the way the workflows run it ────────────────────────────

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _setup(tmp_path: Path, base: bytes, upstream: bytes, local: bytes, *, driver: bool,
           shallow: bool = False):
    """origin/main has `upstream`; a clone pinned at `base` commits `local`, then
    `git pull --rebase` — the exact sequence of every workflow commit step."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "t")
    log = seed / "catalog" / "PROVENANCE_LOG.md"
    log.parent.mkdir()
    (seed / "scripts").mkdir()
    shutil.copy(DRIVER, seed / "scripts" / "merge_append_log.py")
    shutil.copy(REPO_ROOT / ".gitattributes", seed / ".gitattributes")
    log.write_bytes(base)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "main")

    runner = tmp_path / "runner"  # the workflow's checkout, pinned at `base`
    # shallow=True mirrors actions/checkout's default fetch-depth: 1 (the state
    # and legislation jobs) — the rebase must still find the merge base.
    clone = ["clone", "-q", "--depth", "1", f"file://{origin}"] if shallow else ["clone", "-q", str(origin)]
    _git(tmp_path, *clone, str(runner))
    _git(runner, "config", "user.email", "t@example.com")
    _git(runner, "config", "user.name", "t")

    log.write_bytes(upstream)  # another workflow pushes first
    _git(seed, "commit", "-qam", "upstream")
    _git(seed, "push", "-q", "origin", "main")

    (runner / "catalog" / "PROVENANCE_LOG.md").write_bytes(local)
    _git(runner, "commit", "-qam", "local")
    if driver:
        _git(runner, "config", "merge.append-log.driver",
             "python3 scripts/merge_append_log.py %O %A %B %P")
    return runner


@needs_git
def test_rebase_with_driver_resolves_concurrent_appends(tmp_path):
    runner = _setup(tmp_path, BASE, BASE + FED, BASE + IL, driver=True)
    r = _git(runner, "pull", "--rebase", "-q", "origin", "main")
    assert r.returncode == 0, r.stderr
    assert (runner / "catalog" / "PROVENANCE_LOG.md").read_bytes() == BASE + FED + IL
    assert _git(runner, "push", "-q", "origin", "HEAD:main").returncode == 0


@needs_git
def test_rebase_on_the_real_log_reproduces_the_2026_11_09_failure_and_fixes_it(tmp_path):
    """The real active log + realistic blocks: conflicts without the driver
    (the incident), merges byte-exactly with it."""
    real = (REPO_ROOT / "catalog" / "PROVENANCE_LOG.md").read_bytes()
    up, loc = real + FED, real + IL

    no_driver = _setup(tmp_path / "a", real, up, loc, driver=False)
    r = _git(no_driver, "pull", "--rebase", "-q", "origin", "main")
    assert r.returncode != 0 and "CONFLICT" in (r.stdout + r.stderr)

    with_driver = _setup(tmp_path / "b", real, up, loc, driver=True)
    r = _git(with_driver, "pull", "--rebase", "-q", "origin", "main")
    assert r.returncode == 0, r.stderr
    assert (with_driver / "catalog" / "PROVENANCE_LOG.md").read_bytes() == real + FED + IL


@needs_git
def test_rebase_from_a_depth_1_checkout_resolves_concurrent_appends(tmp_path):
    runner = _setup(tmp_path, BASE, BASE + FED, BASE + IL, driver=True, shallow=True)
    assert (runner / ".git" / "shallow").exists()
    r = _git(runner, "pull", "--rebase", "-q", "origin", "main")
    assert r.returncode == 0, r.stderr
    assert (runner / "catalog" / "PROVENANCE_LOG.md").read_bytes() == BASE + FED + IL


@needs_git
def test_rebase_against_a_rotation_still_conflicts(tmp_path):
    rotated = b"# PROVENANCE_LOG\n\nPreamble.\n" + FED
    runner = _setup(tmp_path, BASE, rotated, BASE + IL, driver=True)
    r = _git(runner, "pull", "--rebase", "-q", "origin", "main")
    assert r.returncode != 0
    text = (runner / "catalog" / "PROVENANCE_LOG.md").read_text()
    assert "<<<<<<<" in text and ">>>>>>>" in text


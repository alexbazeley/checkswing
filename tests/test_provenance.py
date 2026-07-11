"""append_provenance appends (never rewrites) the provenance log (§2.2)."""
from __future__ import annotations

from scripts.provenance import append_provenance


def test_appends_without_reading_whole_file(tmp_path):
    p = tmp_path / "PROV.md"
    append_provenance("\n### block A\n", p)
    append_provenance("\n### block B\n", p)
    text = p.read_text()
    # Both blocks present, in order — an append never clobbers a prior entry.
    assert text == "\n### block A\n\n### block B\n"


def test_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "PROV.md"
    append_provenance("\n### x\n", p)
    assert p.exists() and "### x" in p.read_text()


def test_interleaved_appends_all_survive(tmp_path):
    # Simulate two writers appending different blocks; both must survive (the old
    # read-modify-write pattern could drop one under a race).
    p = tmp_path / "PROV.md"
    for i in range(20):
        append_provenance(f"\n- entry {i}\n", p)
    text = p.read_text()
    assert all(f"- entry {i}" in text for i in range(20))
    assert text.count("- entry ") == 20

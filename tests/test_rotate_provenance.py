"""Tests for §2.2 provenance log rotation + corpus parsing."""
from __future__ import annotations

from pathlib import Path

from scripts.parse_provenance import entries_region, parse_provenance_corpus
from scripts.rotate_provenance import apply_rotation, plan_rotation, split_log


PREAMBLE = "# PROVENANCE_LOG\n\nHuman-readable header, not an entry.\n"
E2024 = "\n### 2024-03-01T00:00Z — INGESTION — old year\n\nBody for 2024.\n"
E2025 = "\n### 2025-07-04T12:00Z — NOTE — mid year\n\n- a 2025 bullet\n"
E2026A = "\n### 2026-01-02T09:00Z — SIGNAL_CHANGE — this year one\n\nBody 2026a.\n"
E2026B = "\n### 2026-05-22T00:00Z — SETUP — this year two\n\nBody 2026b.\n"
FULL = PREAMBLE + E2024 + E2025 + E2026A + E2026B


class TestSplitLog:
    def test_splits_preamble_and_blocks_with_years(self):
        preamble, blocks = split_log(FULL)
        assert "Human-readable header" in preamble
        assert [y for y, _ in blocks] == [2024, 2025, 2026, 2026]
        # No text is lost: preamble + all blocks reconstruct the original exactly
        # (block boundaries land on the heading line, so the blank line before a
        # heading rides on the previous block — verbatim at the whole-text level).
        assert preamble + "".join(b for _, b in blocks) == FULL
        assert blocks[0][1].startswith("### 2024-03-01")
        assert "Body for 2024." in blocks[0][1]

    def test_no_headings_returns_all_as_preamble(self):
        preamble, blocks = split_log("just prose\nno entries\n")
        assert blocks == []
        assert preamble == "just prose\nno entries\n"


class TestPlanRotation:
    def test_seals_years_before_cutoff_only(self):
        plan = plan_rotation(FULL, cutoff_year=2026)
        assert plan["years"] == {2024: 1, 2025: 1}
        assert plan["n_archived"] == 2
        assert plan["n_retained"] == 2
        # Retained active log keeps the preamble + both 2026 entries, in order.
        assert "Human-readable header" in plan["new_active_text"]
        assert E2026A in plan["new_active_text"]
        assert E2026B in plan["new_active_text"]
        assert E2024 not in plan["new_active_text"]
        assert E2025 not in plan["new_active_text"]

    def test_nothing_to_seal_when_all_current(self):
        plan = plan_rotation(PREAMBLE + E2026A + E2026B, cutoff_year=2026)
        assert plan["n_archived"] == 0
        assert plan["years"] == {}


class TestCorpusCompleteness:
    """The whole point: parse(active) before == parse(active + archives) after."""

    def test_rotation_preserves_every_entry(self, tmp_path):
        active = tmp_path / "PROVENANCE_LOG.md"
        archive_dir = tmp_path / "provenance"
        active.write_text(FULL, encoding="utf-8")

        before = parse_provenance_corpus(active, archive_dir)
        assert [e["subject"] for e in before] == [
            "old year", "mid year", "this year one", "this year two",
        ]

        plan = apply_rotation(
            cutoff_year=2026, active_path=active, archive_dir=archive_dir, apply=True
        )
        assert plan["applied"] is True
        assert plan["n_archived"] == 2

        # Active log shrank to 2026 + a rotation NOTE; archives hold 2024/2025.
        assert (archive_dir / "PROVENANCE_LOG-2024.md").exists()
        assert (archive_dir / "PROVENANCE_LOG-2025.md").exists()
        active_text = active.read_text(encoding="utf-8")
        assert "old year" not in active_text
        assert "provenance log rotation" in active_text  # the ROTATION note

        # Corpus after rotation still yields every original entry, in order,
        # plus the new rotation note appended to the active (current-year) log.
        after = parse_provenance_corpus(active, archive_dir)
        subjects = [e["subject"] for e in after]
        assert subjects[:4] == [
            "old year", "mid year", "this year one", "this year two",
        ]
        assert any("rotation" in s for s in subjects)

    def test_dry_run_touches_nothing(self, tmp_path):
        active = tmp_path / "PROVENANCE_LOG.md"
        archive_dir = tmp_path / "provenance"
        active.write_text(FULL, encoding="utf-8")
        plan = apply_rotation(
            cutoff_year=2026, active_path=active, archive_dir=archive_dir, apply=False
        )
        assert plan["applied"] is False
        assert plan["n_archived"] == 2
        assert active.read_text(encoding="utf-8") == FULL  # unchanged
        assert not archive_dir.exists()


class TestEntriesRegion:
    def test_drops_preamble_keeps_from_first_heading(self):
        region = entries_region(FULL)
        assert region.startswith("### 2024-03-01")
        assert "Human-readable header" not in region

    def test_no_archive_dir_matches_plain_parse(self, tmp_path):
        active = tmp_path / "PROVENANCE_LOG.md"
        active.write_text(FULL, encoding="utf-8")
        # archive_dir absent → identical to parsing the active log alone.
        entries = parse_provenance_corpus(active, tmp_path / "nope")
        assert len(entries) == 4

"""Tests for matrix YAML/log adoption (scripts/finalize_matrix.py).

Each bucket diffs against the PRE-refresh snapshot (not the partially-merged
state), so applying bucket B can't clobber bucket A. These pin adoption,
append-only log suffixing, and the non-prefix mismatch guard.
"""
from __future__ import annotations

from pathlib import Path

from scripts.finalize_matrix import _adopt_yamls, _append_log_suffix, main as finalize_main


def test_adopt_only_changed_yaml(tmp_path):
    base = tmp_path / "base"
    (base / "owners").mkdir(parents=True)
    (base / "owners" / "x.yaml").write_text("A", encoding="utf-8")
    (base / "owners" / "y.yaml").write_text("Y", encoding="utf-8")

    bucket = tmp_path / "bucket"
    (bucket / "owners").mkdir(parents=True)
    (bucket / "owners" / "x.yaml").write_text("A-changed", encoding="utf-8")  # differs
    (bucket / "owners" / "y.yaml").write_text("Y", encoding="utf-8")  # unchanged

    original = {"x.yaml": "A", "y.yaml": "Y"}
    adopted = _adopt_yamls(base, bucket, original)

    assert adopted == ["x"]
    assert (base / "owners" / "x.yaml").read_text(encoding="utf-8") == "A-changed"
    assert (base / "owners" / "y.yaml").read_text(encoding="utf-8") == "Y"


def test_append_log_suffix_appends_delta(tmp_path):
    base_text = "line1\n"
    bucket_file = tmp_path / "bucket_log.md"
    bucket_file.write_text("line1\nline2\n", encoding="utf-8")
    target = tmp_path / "target_log.md"
    target.write_text(base_text, encoding="utf-8")

    added = _append_log_suffix(base_text, bucket_file, target, label="TEST")
    assert added == 1
    assert target.read_text(encoding="utf-8") == "line1\nline2\n"


def test_append_log_suffix_skips_non_prefix(tmp_path, capsys):
    base_text = "line1\n"
    bucket_file = tmp_path / "bucket_log.md"
    bucket_file.write_text("DIFFERENT\nline2\n", encoding="utf-8")  # does not start with base
    target = tmp_path / "target_log.md"
    target.write_text(base_text, encoding="utf-8")

    added = _append_log_suffix(base_text, bucket_file, target, label="TEST")
    assert added == 0
    # Target untouched.
    assert target.read_text(encoding="utf-8") == base_text


def test_append_log_suffix_noop_when_equal(tmp_path):
    base_text = "same\n"
    bucket_file = tmp_path / "bucket_log.md"
    bucket_file.write_text("same\n", encoding="utf-8")
    target = tmp_path / "target_log.md"
    target.write_text(base_text, encoding="utf-8")
    assert _append_log_suffix(base_text, bucket_file, target, label="TEST") == 0


def test_main_adopts_yaml_and_appends_log(tmp_path):
    base = tmp_path / "base"
    (base / "owners").mkdir(parents=True)
    (base / "catalog").mkdir(parents=True)
    (base / "owners" / "x.yaml").write_text("orig\n", encoding="utf-8")
    (base / "catalog" / "PROVENANCE_LOG.md").write_text("PROV-base\n", encoding="utf-8")
    (base / "catalog" / "REVIEW_QUEUE.md").write_text("REV-base\n", encoding="utf-8")

    artifacts = tmp_path / "artifacts"
    b0 = artifacts / "refresh-bucket-0"
    (b0 / "owners").mkdir(parents=True)
    (b0 / "catalog").mkdir(parents=True)
    (b0 / "owners" / "x.yaml").write_text("orig\nchanged\n", encoding="utf-8")
    (b0 / "catalog" / "PROVENANCE_LOG.md").write_text("PROV-base\nPROV-new\n", encoding="utf-8")
    (b0 / "catalog" / "REVIEW_QUEUE.md").write_text("REV-base\nREV-new\n", encoding="utf-8")

    rc = finalize_main(["--base", str(base), "--artifacts", str(artifacts)])
    assert rc == 0
    assert (base / "owners" / "x.yaml").read_text(encoding="utf-8") == "orig\nchanged\n"
    assert "PROV-new" in (base / "catalog" / "PROVENANCE_LOG.md").read_text(encoding="utf-8")
    assert "REV-new" in (base / "catalog" / "REVIEW_QUEUE.md").read_text(encoding="utf-8")

"""Tests for surfacing per-owner matrix refresh failures (scripts/summarize_refresh.py).

The bucket refresh step is continue-on-error, so per-owner failures never turn
the job red; these pin that the consolidate-side report finds them, flags a
bucket that crashed before writing a summary, and never fails the step itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from scripts import cli as cli_mod
from scripts.summarize_refresh import collect, main as summarize_main


def _bucket(artifacts: Path, idx: int, summary: dict | None) -> None:
    d = artifacts / f"refresh-bucket-{idx}"
    d.mkdir(parents=True)
    if summary is not None:
        (d / "refresh-summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _summary(ok: list[str], failed: dict[str, str]) -> dict:
    per_owner = {s: {"status": "ok"} for s in ok}
    per_owner.update({s: {"status": "error", "error": e} for s, e in failed.items()})
    return {
        "owners_attempted": len(ok) + len(failed),
        "owners_succeeded": len(ok),
        "owners_failed": len(failed),
        "failed_owners": list(failed),
        "per_owner": per_owner,
    }


def test_collects_failures_across_buckets(tmp_path):
    _bucket(tmp_path, 0, _summary(["a"], {"castellini-bob": "FEC 504: Query timed out"}))
    _bucket(tmp_path, 1, _summary(["b", "c"], {}))
    _bucket(tmp_path, 2, _summary([], {"cohen-steven": "FEC 504"}))

    result = collect(tmp_path)

    assert result["owners_attempted"] == 5
    assert result["owners_succeeded"] == 3
    assert result["failed"] == {
        "castellini-bob": "FEC 504: Query timed out",
        "cohen-steven": "FEC 504",
    }
    assert result["buckets_without_summary"] == []


def test_bucket_without_summary_is_reported(tmp_path):
    _bucket(tmp_path, 0, _summary(["a"], {}))
    _bucket(tmp_path, 3, None)  # artifact uploaded, refresh step crashed first

    assert collect(tmp_path)["buckets_without_summary"] == [3]


def test_main_writes_outputs_and_never_fails(tmp_path, monkeypatch, capsys):
    artifacts = tmp_path / "artifacts"
    _bucket(artifacts, 0, _summary([], {"kendrick-ken": "line1\nline2 | 100%"}))
    out = tmp_path / "out"
    step = tmp_path / "step.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))

    assert summarize_main(["--artifacts", str(artifacts)]) == 0

    outputs = out.read_text(encoding="utf-8")
    assert "owners_failed=1\n" in outputs
    assert "failed_owners=kendrick-ken\n" in outputs
    # Annotation message is escaped so the newline can't end the command early.
    assert "::error title=Refresh failed: kendrick-ken::line1%0Aline2 | 100%25" in capsys.readouterr().out
    # The table cell can't break the markdown table.
    assert "| `kendrick-ken` | line1 line2 \\| 100% |" in step.read_text(encoding="utf-8")


def test_clean_run_reports_zero(tmp_path, monkeypatch):
    _bucket(tmp_path / "a", 0, _summary(["x"], {}))
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert summarize_main(["--artifacts", str(tmp_path / "a")]) == 0
    assert "owners_failed=0\nfailed_owners=\n" in out.read_text(encoding="utf-8")


def test_cli_refresh_writes_summary_json_even_on_failure(tmp_path, monkeypatch):
    summary = _summary(["a"], {"b": "boom"})
    monkeypatch.setattr(cli_mod, "refresh_all", lambda **_k: summary)
    target = tmp_path / "refresh-summary.json"

    res = CliRunner().invoke(cli_mod.cli, ["refresh", "--only", "a,b", "--summary-json", str(target)])

    assert res.exit_code == 1  # owner failure still exits non-zero
    assert json.loads(target.read_text(encoding="utf-8"))["failed_owners"] == ["b"]

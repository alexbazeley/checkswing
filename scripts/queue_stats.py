"""Read-only review-queue burndown — the operational-health view across owners.

Used by the `queue-stats` CLI command. Where `audit <slug>` is a deep, single-
owner calibration aid, this is the wide, cross-owner (and cross-jurisdiction)
triage surface the project review (#7) asked for: how much UNCERTAIN work is
*open* vs already adjudicated, where it's concentrated, and which owners are the
highest-leverage calibration targets (by P/C ratio and open-queue size).

Two layers, mirroring the two databases:
  - Federal (`master.db`): per-owner CONFIRMED / PROBABLE / P-over-C ratio,
    open vs resolved review-queue counts, last-ingestion timestamp, and an
    open-reason histogram.
  - State (`state.db`, if present): open vs resolved per jurisdiction and per
    owner, plus an open-reason histogram.

Nothing here writes. Everything is sourced from columns already in the DB
(no raw-payload reads), so it is fast and never depends on raw coverage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tabulate import tabulate

from . import db, state_db
from .paths import STATE_DB


# ─── Data model ──────────────────────────────────────────────────────────────


@dataclass
class OwnerQueueRow:
    slug: str
    name: str
    confirmed: int
    probable: int
    open_queue: int
    resolved_queue: int
    last_ingestion: str | None

    @property
    def pc_ratio(self) -> float | None:
        """PROBABLE / CONFIRMED. None when there are no CONFIRMED rows (ratio
        undefined); a high value flags loose signals worth calibrating."""
        if self.confirmed:
            return self.probable / self.confirmed
        return None


@dataclass
class FederalQueueStats:
    total_open: int
    total_resolved: int
    owners: list[OwnerQueueRow]
    reasons: list[tuple[str, int]]

    @property
    def pct_adjudicated(self) -> float | None:
        total = self.total_open + self.total_resolved
        return (self.total_resolved / total) if total else None


@dataclass
class StateQueueStats:
    total_open: int
    total_resolved: int
    by_jurisdiction: list[tuple[str, int, int]]  # (jurisdiction, open, resolved)
    by_owner: list[tuple[str, int]]              # (slug, open), open desc
    reasons: list[tuple[str, int]]               # (reason, open count)

    @property
    def pct_adjudicated(self) -> float | None:
        total = self.total_open + self.total_resolved
        return (self.total_resolved / total) if total else None


@dataclass
class QueueStats:
    federal: FederalQueueStats
    state: StateQueueStats | None = None
    generated_at: str = field(default="")


# ─── Build ───────────────────────────────────────────────────────────────────


def _build_federal(db_path: Path | None = None) -> FederalQueueStats:
    db_kwargs = {"db_path": db_path} if db_path else {}
    with db.connect(**db_kwargs) as conn:
        cur = conn.cursor()

        donation_counts: dict[str, tuple[int, int]] = {}
        for r in cur.execute(
            """
            SELECT entity_slug,
                   SUM(status = 'CONFIRMED') AS confirmed,
                   SUM(status = 'PROBABLE')  AS probable
            FROM donations
            GROUP BY entity_slug
            """
        ).fetchall():
            donation_counts[r["entity_slug"]] = (r["confirmed"] or 0, r["probable"] or 0)

        queue_counts: dict[str, tuple[int, int]] = {}
        for r in cur.execute(
            """
            SELECT entity_slug,
                   SUM(resolution IS NULL)     AS open_q,
                   SUM(resolution IS NOT NULL) AS resolved_q
            FROM review_queue
            GROUP BY entity_slug
            """
        ).fetchall():
            queue_counts[r["entity_slug"]] = (r["open_q"] or 0, r["resolved_q"] or 0)

        names = {
            r["slug"]: (r["name"] or r["slug"])
            for r in cur.execute("SELECT slug, name FROM entities").fetchall()
        }

        last_ingestion = {
            r["entity_slug"]: r["last_run"]
            for r in cur.execute(
                "SELECT entity_slug, MAX(completed_at) AS last_run "
                "FROM ingestion_runs GROUP BY entity_slug"
            ).fetchall()
        }

        reasons = [
            (r["reason"], r["n"])
            for r in cur.execute(
                """
                SELECT reason, COUNT(*) AS n
                FROM review_queue
                WHERE resolution IS NULL
                GROUP BY reason
                ORDER BY n DESC
                """
            ).fetchall()
        ]

    slugs = set(donation_counts) | set(queue_counts)
    owners: list[OwnerQueueRow] = []
    for slug in slugs:
        confirmed, probable = donation_counts.get(slug, (0, 0))
        open_q, resolved_q = queue_counts.get(slug, (0, 0))
        owners.append(
            OwnerQueueRow(
                slug=slug,
                name=names.get(slug, slug),
                confirmed=confirmed,
                probable=probable,
                open_queue=open_q,
                resolved_queue=resolved_q,
                last_ingestion=last_ingestion.get(slug),
            )
        )

    # Burndown priority: where the open work is, then loosest signals (P/C),
    # then deterministic by slug. None P/C ratio sorts last among ties.
    owners.sort(
        key=lambda o: (
            -o.open_queue,
            -(o.pc_ratio if o.pc_ratio is not None else -1.0),
            o.slug,
        )
    )

    total_open = sum(o.open_queue for o in owners)
    total_resolved = sum(o.resolved_queue for o in owners)
    return FederalQueueStats(
        total_open=total_open,
        total_resolved=total_resolved,
        owners=owners,
        reasons=reasons,
    )


def _build_state(state_db_path: Path | None = None) -> StateQueueStats | None:
    path = state_db_path or STATE_DB
    if not Path(path).exists():
        return None
    db_kwargs = {"db_path": state_db_path} if state_db_path else {}
    with state_db.connect(**db_kwargs) as conn:
        cur = conn.cursor()
        by_jur = [
            (r["jurisdiction"], r["open_q"] or 0, r["resolved_q"] or 0)
            for r in cur.execute(
                """
                SELECT jurisdiction,
                       SUM(resolution IS NULL)     AS open_q,
                       SUM(resolution IS NOT NULL) AS resolved_q
                FROM state_review_queue
                GROUP BY jurisdiction
                ORDER BY open_q DESC, jurisdiction
                """
            ).fetchall()
        ]
        by_owner = [
            (r["entity_slug"], r["open_q"] or 0)
            for r in cur.execute(
                """
                SELECT entity_slug, SUM(resolution IS NULL) AS open_q
                FROM state_review_queue
                GROUP BY entity_slug
                HAVING open_q > 0
                ORDER BY open_q DESC, entity_slug
                """
            ).fetchall()
        ]
        reasons = [
            (r["reason"], r["n"])
            for r in cur.execute(
                """
                SELECT reason, COUNT(*) AS n
                FROM state_review_queue
                WHERE resolution IS NULL
                GROUP BY reason
                ORDER BY n DESC
                """
            ).fetchall()
        ]
    total_open = sum(o for _, o, _ in by_jur)
    total_resolved = sum(r for _, _, r in by_jur)
    return StateQueueStats(
        total_open=total_open,
        total_resolved=total_resolved,
        by_jurisdiction=by_jur,
        by_owner=by_owner,
        reasons=reasons,
    )


def build_queue_stats(
    *, db_path: Path | None = None, state_db_path: Path | None = None
) -> QueueStats:
    """Assemble the federal + (optional) state burndown view. Read-only."""
    return QueueStats(
        federal=_build_federal(db_path),
        state=_build_state(state_db_path),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


# ─── Formatting ──────────────────────────────────────────────────────────────


def _trunc(s: str | None, n: int = 70) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "—"


def _ratio(x: float | None) -> str:
    return f"{x:.2f}" if x is not None else "—"


def _age_days(ts: str | None, now: datetime) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return "?"
    return f"{(now - dt).days}d"


def format_queue_stats(stats: QueueStats, *, top: int = 0) -> str:
    """Render the burndown for the terminal. `top` limits the per-owner table
    (0 = show all owners with any open queue, plus the rest collapsed)."""
    now = datetime.now(timezone.utc)
    parts: list[str] = []
    f = stats.federal

    parts.append("\n======== REVIEW-QUEUE BURNDOWN ========")
    parts.append(
        f"\nFEDERAL (master.db): {f.total_open:,} open · "
        f"{f.total_resolved:,} resolved · {_pct(f.pct_adjudicated)} adjudicated"
    )

    owners_with_open = [o for o in f.owners if o.open_queue > 0]
    shown = owners_with_open if owners_with_open else f.owners[:10]
    if top:
        shown = shown[:top]
    if shown:
        rows = [
            [
                o.slug,
                o.confirmed,
                o.probable,
                _ratio(o.pc_ratio),
                o.open_queue,
                o.resolved_queue,
                _age_days(o.last_ingestion, now),
            ]
            for o in shown
        ]
        parts.append(
            tabulate(
                rows,
                headers=["owner", "CONF", "PROB", "P/C", "open", "resolved", "ingest age"],
                tablefmt="simple",
            )
        )
        if owners_with_open:
            parts.append(f"\n  {len(owners_with_open)} owner(s) with open queue items.")
        else:
            parts.append("\n  No open federal queue items — showing top owners by volume.")
    else:
        parts.append("  (no owners in the DB yet)")

    if f.reasons:
        parts.append("\nFEDERAL OPEN-QUEUE REASONS")
        parts.append(
            tabulate(
                [[n, _trunc(reason, 80)] for reason, n in f.reasons],
                headers=["n", "reason"],
                tablefmt="simple",
            )
        )

    s = stats.state
    if s is None:
        parts.append("\nSTATE (state.db): not present.")
    else:
        parts.append(
            f"\nSTATE (state.db): {s.total_open:,} open · "
            f"{s.total_resolved:,} resolved · {_pct(s.pct_adjudicated)} adjudicated"
        )
        if s.by_jurisdiction:
            parts.append("\nSTATE QUEUE BY JURISDICTION")
            parts.append(
                tabulate(
                    [[j, o, r] for j, o, r in s.by_jurisdiction],
                    headers=["juris", "open", "resolved"],
                    tablefmt="simple",
                )
            )
        if s.by_owner:
            owner_rows = s.by_owner[:top] if top else s.by_owner
            parts.append("\nSTATE QUEUE BY OWNER (open, desc)")
            parts.append(
                tabulate(
                    [[slug, n] for slug, n in owner_rows],
                    headers=["owner", "open"],
                    tablefmt="simple",
                )
            )
        if s.reasons:
            parts.append("\nSTATE OPEN-QUEUE REASONS (top 10)")
            parts.append(
                tabulate(
                    [[n, _trunc(reason, 80)] for reason, n in s.reasons[:10]],
                    headers=["n", "reason"],
                    tablefmt="simple",
                )
            )

    parts.append("")
    parts.append("(read-only. Adjudicate with `resolve` / `bulk-discard`, or tighten")
    parts.append(" signals per docs/CALIBRATION_PLAYBOOK.md. `audit <slug>` drills in.)")
    return "\n".join(parts)


def queue_stats_report(*, top: int = 0) -> str:
    """Top-level entry point: build + format the burndown."""
    return format_queue_stats(build_queue_stats(), top=top)

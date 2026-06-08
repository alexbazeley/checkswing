"""Fetch + parse the Minnesota CFB campaign-finance bulk export.

The Minnesota Campaign Finance and Public Disclosure Board publishes contribution
datasets as plain CSVs behind a `?download=<id>` link on the data-downloads page
(no login/API key). We ingest the broadest individual-contribution dataset,
"Contributions received by all entities — 2015 to present": a single comma-
delimited, header-bearing CSV. Unlike PA/CO it is ONE cumulative file (not per-
year zips), so each refresh re-pulls the whole file and the idempotent content-
hash upsert reconciles it.

The recipient (Recipient / Recipient type / Recipient reg num) is INLINE on every
row, so there is no filer index — the resolver is a stateless reader (like CO).
MN has no per-contribution id, so a stable content-hash `_tran` is stamped for
keying + dedup (like PA).

The `?download=<id>` numbers are content hashes of the dataset definition, not a
stable constructable URL, so `download_latest` resolves the current id by reading
the data-downloads page and matching the dataset's anchor text — more robust than
hardcoding an id that could rotate when MN regenerates the page. Parsing + the
resolver are pure and unit-tested against fixtures; only the network download is
untested (same split as the CA/PA/CO fetchers). The downloaded CSV is the ground
truth (GOVERNANCE.md §1.4), gitignored under data/raw/state/mn/.
"""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Callable, Iterable, Iterator, TextIO

from .mn_adapter import _clean, recipient_type_of, surname_of

csv.field_size_limit(10_000_000)

DOWNLOAD_PAGE = (
    "https://cfb.mn.gov/reports-and-data/self-help/data-downloads/campaign-finance/"
)
# The dataset we ingest — its anchor text on the downloads page. "all entities"
# is the union of candidates + committees + party units (the broadest individual-
# contribution receipts feed), 2015–present.
DEFAULT_DATASET = "all entities"


def _is_contrib_csv(name: str) -> bool:
    return name.lower().endswith(".csv")


# ── Pure parsers (operate on a text handle; path wrappers below) ──────────────

def _reader(fh: TextIO) -> csv.DictReader:
    return csv.DictReader(fh)


def _content_tran(row: dict) -> str:
    """Stable per-contribution hash (MN has no native id). Over the content fields
    that identify a single receipt — idempotent across re-downloads."""
    parts = "|".join(
        _clean(row.get(k))
        for k in (
            "Recipient reg num", "Contributor", "Amount", "Receipt date",
            "Contrib zip", "Contrib Employer name", "Receipt type",
        )
    )
    return hashlib.sha1(parts.encode("utf-8")).hexdigest()[:16]


def iter_contributions_fh(fh: TextIO) -> Iterator[dict]:
    """Yield one recipient-joined, hash-stamped contribution dict per CSV row."""
    for row in _reader(fh):
        out = dict(row)
        out["_recipient_name"] = _clean(row.get("Recipient"))
        out["_recipient_type"] = recipient_type_of(row)
        out["_tran"] = _content_tran(row)
        yield out


def _open_text(path: Path) -> TextIO:
    # utf-8-sig strips a possible BOM on the header's first column name.
    return path.open("r", encoding="utf-8-sig", errors="replace", newline="")


# ── File discovery + streaming ───────────────────────────────────────────────

def find_mn_files(input_dir: Path) -> list[Path]:
    return [p for p in sorted(Path(input_dir).rglob("*")) if p.is_file() and _is_contrib_csv(p.name)]


def iter_contributions(contrib_csv: Path) -> Iterator[dict]:
    fh = _open_text(contrib_csv)
    try:
        yield from iter_contributions_fh(fh)
    finally:
        fh.close()


def iter_dir(input_dir: Path) -> Iterator[dict]:
    """Stream every contribution across an input dir (one or more `*.csv`)."""
    for c in find_mn_files(Path(input_dir)):
        yield from iter_contributions(c)


# ── Recipient resolver / bucketing / dedup ───────────────────────────────────

def make_recipient_resolver(*_args) -> Callable[[dict], dict]:
    """Recipient is pre-joined onto each row by iter_contributions; just read it.

    Accepts (and ignores) a positional arg to satisfy the StateSource
    `recipient_resolver(input_path)` registry signature.
    """

    def _resolve(row: dict) -> dict:
        return {
            "filer_id": _clean(row.get("Recipient reg num")) or None,
            "name": _clean(row.get("_recipient_name")),
            "type": row.get("_recipient_type"),
        }

    return _resolve


def _surname_set(owner: dict) -> set[str]:
    surnames: set[str] = set()
    for v in owner.get("name_variants") or []:
        v = (v or "").strip()
        if not v:
            continue
        surnames.add((v.split(",")[0] if "," in v else v.split()[-1]).strip().lower())
    return {s for s in surnames if s}


def bucket_rows_by_owner(input_dir_or_rows, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Single streaming pass → {slug: [candidate rows]} across owners.

    Accepts either an input dir (the live source — streams the CSV) or an already-
    built iterable of rows (tests). Each kept row already carries the inline
    recipient fields + the content-hash `_tran`.
    """
    if isinstance(input_dir_or_rows, (str, Path)):
        rows: Iterable[dict] = iter_dir(Path(input_dir_or_rows))
    else:
        rows = input_dir_or_rows

    surname_map = [(slug, _surname_set(o)) for slug, o in owners]
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    for row in rows:
        sn = surname_of(row)
        if not sn:
            continue
        for slug, sns in surname_map:
            if sns and any(s in sn for s in sns):
                buckets[slug].append(row)
    return buckets


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on the content-hash tran id (idempotent across re-downloads)."""
    seen: dict[str, dict] = {}
    for row in rows:
        key = _clean(row.get("_tran")) or id(row)
        seen.setdefault(key, row)
    return list(seen.values())


# ── Download (live cfb.mn.gov CSV) ───────────────────────────────────────────

def resolve_download_url(page_html: str, dataset: str = DEFAULT_DATASET) -> str | None:
    """Find the `?download=<id>` URL whose anchor text contains `dataset`.

    Pure (testable on fixture HTML). Returns an absolute cfb.mn.gov URL, or None
    if no matching dataset link is present.
    """
    want = dataset.lower()
    # Anchors are `<a href="...?download=<id>">Contributions received by all entities …</a>`.
    for m in re.finditer(r'<a[^>]+href="([^"]*\?download=[-0-9]+)"[^>]*>(.*?)</a>',
                          page_html, re.IGNORECASE | re.DOTALL):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if want in text.lower():
            if href.startswith("http"):
                return href
            return "https://cfb.mn.gov" + (href if href.startswith("/") else "/" + href)
    return None


def download_latest(dest: Path | None = None, dataset: str = DEFAULT_DATASET) -> Path:  # pragma: no cover - network
    """Resolve the current dataset id from the downloads page + fetch the CSV into
    data/raw/state/mn/. Returns the downloaded CSV path (stream it with iter_dir)."""
    import shutil
    from urllib.request import Request, urlopen

    from .paths import state_raw_dir
    out_dir = dest or state_raw_dir("mn")
    out_dir.mkdir(parents=True, exist_ok=True)

    req = Request(DOWNLOAD_PAGE, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req) as resp:  # noqa: S310
        page = resp.read().decode("utf-8", errors="replace")
    url = resolve_download_url(page, dataset)
    if not url:
        raise RuntimeError(f"Could not resolve MN '{dataset}' download link on {DOWNLOAD_PAGE}")

    csv_path = out_dir / "contributions.csv"
    dl = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(dl) as resp, csv_path.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    return csv_path

#!/usr/bin/env bash
# Resumable, version-pinned download of one ISBE bulk file (il-backfill.yml).
#
#   usage: scripts/resumable_download.sh URL DEST DEADLINE_EPOCH
#   exit:  0 = DEST is a complete copy of ONE published version of URL
#          1 = deadline reached (partial kept for the next run) or origin unreachable
#
# ISBE drops the connection every few minutes on its ~1 GB Receipts.txt (the
# 2026-11-08 run resumed ~45 times over 3h20m), so the download resumes with
# `curl -C -`, and the partial is cached across runs. Two properties the earlier
# inline loop did not have:
#
# 1. Never splice two exports. ISBE republishes the files daily. Resuming a
#    partial from yesterday's export with today's bytes produces a file that is
#    neither — and nothing downstream would notice. The published version
#    (size + Last-Modified) is recorded in DEST.version next to the partial; a
#    partial whose recorded version differs from what ISBE now serves (or that
#    has no record) is discarded, not resumed. Each resume also sends
#    `If-Range: <Last-Modified>`: if the file changed since the probe, ISBE
#    answers 200 instead of 206 and curl refuses to append (exit 33, partial
#    untouched — verified), and the next probe restarts the download cleanly.
#
# 2. Don't depend on HEAD. Through Cloudflare, ISBE's HEAD response often omits
#    Content-Length and returns a weak, varying ETag (seen 2026-11-09: 8 of 8 HEADs
#    with no Content-Length) — the old loop read Content-Length from HEAD and
#    failed with "ISBE unreachable". A 1-byte ranged GET is stable: it always
#    returns 206 with `Content-Range: bytes 0-0/<total>` and Last-Modified. ETag is
#    not used as the version: it differs between Cloudflare paths for the same file.
#
# CURL_EXTRA (tests only) is appended to every curl call, e.g. "--noproxy *".
set -uo pipefail

url=$1
dest=$2
deadline=$3
ver="$dest.version"
name=$(basename "$dest")
read -r -a extra <<< "${CURL_EXTRA:-}"

# Prints "<total-bytes>|<Last-Modified>" for the published file, or nothing.
probe() {
  curl -sS --http1.1 --max-time 60 --max-filesize 1048576 "${extra[@]}" \
      -r 0-0 -D - -o /dev/null "$url" 2>/dev/null \
    | tr -d '\r' \
    | awk 'tolower($1)=="content-range:" { n = split($3, a, "/"); total = a[n] }
           tolower($1)=="last-modified:" { sub(/^[^:]*:[ \t]*/, ""); lm = $0 }
           END { if (total ~ /^[0-9]+$/ && lm != "") print total "|" lm }'
}

discard() {  # $1 = reason
  echo "::warning::$name: $1 — discarding the partial and starting over (resuming across versions would splice two exports)."
  rm -f "$dest"
}

current=$(probe)
if [ -z "$current" ]; then
  echo "::error::$name: could not read size + Last-Modified from a ranged GET — ISBE unreachable, or no longer honouring Range."
  exit 1
fi

mkdir -p "$(dirname "$dest")"
if [ -e "$dest" ]; then
  recorded=$(cat "$ver" 2>/dev/null || echo "")
  if [ -z "$recorded" ]; then
    discard "cached partial has no recorded export version"
  elif [ "$recorded" != "$current" ]; then
    discard "cached partial is from export [$recorded], ISBE now serves [$current]"
  fi
fi
printf '%s\n' "$current" > "$ver"
echo "$name: export [$current]"

while :; do
  expected=${current%%|*}
  lm=${current#*|}
  have=$(stat -c%s "$dest" 2>/dev/null || echo 0)

  if [ "$have" -gt "$expected" ]; then
    discard "partial ($have bytes) is larger than the published file ($expected)"
    continue
  fi
  if [ "$have" -eq "$expected" ]; then
    # Confirm the export did not change under us between the last chunk and now.
    now=$(probe)
    if [ -n "$now" ] && [ "$now" != "$current" ]; then
      discard "ISBE republished while downloading ([$current] -> [$now])"
      current=$now
      printf '%s\n' "$current" > "$ver"
      continue
    fi
    echo "$name: complete ($have bytes, export [$current])"
    exit 0
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "::warning::Deadline reached with $name at $have/$expected bytes. The partial is cached with its export version — re-run to resume (it is discarded automatically if ISBE has republished)."
    exit 1
  fi

  echo "$name: $have/$expected — resuming"
  curl -fsS --http1.1 -C - -H "If-Range: $lm" --connect-timeout 30 \
    --speed-limit 5000 --speed-time 120 "${extra[@]}" \
    -o "$dest" "$url" || true
  sleep "${RESUME_SLEEP:-5}"

  now=$(probe)
  if [ -n "$now" ] && [ "$now" != "$current" ]; then
    discard "ISBE republished while downloading ([$current] -> [$now])"
    current=$now
    printf '%s\n' "$current" > "$ver"
  fi
done

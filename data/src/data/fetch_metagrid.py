"""Fetch the authority/reference links that Metagrid holds for the persons of birth_death_places_geo.csv.

The ARK landing pages (archives-quickaccess.ch/bbb/person/<id>) embed a Metagrid widget that loads its links from

    https://api.metagrid.ch/widget/burgerbibliothek/person/<person_id>.json?lang=de

so the links (Historisches Lexikon der Schweiz, GND, VIAF, LoC, Helveticat, hallerNet, ...) are *not* in the cached
HTML. This script queries that API once per person and writes `person_metagrid.csv`:

    person_id, ark, n_links, <one column per provider with the URL>, ...

Provider columns are snake_cased Metagrid provider keys (`hls`, `gnd`, `viaf`, `loc`, `elites_suisses`, ...), most frequent first.
A 404 from Metagrid means "no links known for this person" (all provider columns empty).
`person_id` = `persons_descriptors.id`, the same key as in person_enrichment.csv.

Raw API responses are cached in `metagrid_cache/` (git-ignored), so the script is resumable.

Usage:
    python fetch_metagrid.py [--limit N] [--workers 3] [--delay 0.2] [--refresh] [--offline]
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl
import requests

HERE = Path(__file__).parent
CSV_PATH = HERE / "birth_death_places_geo.csv"
PERSONS_PATH = HERE / "persons_descriptors.parquet"
CACHE_DIR = HERE / "metagrid_cache"
OUT_PATH = HERE / "person_metagrid.csv"

API = "https://api.metagrid.ch/widget/burgerbibliothek/person/{id}.json"
USER_AGENT = "GLAMHack26-LivesInSpace/0.1 (hackathon data enrichment; github.com/burgerbibliothek/glamhack26)"

_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
    return _local.s


def fetch(person_id: int, refresh: bool, offline: bool, delay: float, retries: int = 3) -> dict[str, str] | None:
    """Return {provider: url}. Empty dict if Metagrid knows no links (404). None if the request failed."""
    path = CACHE_DIR / f"{person_id}.json"
    if path.exists() and not refresh:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    if offline:
        return None
    for attempt in range(retries):
        try:
            r = _session().get(API.format(id=person_id), params={"lang": "de"}, timeout=30)
            if r.status_code in (200, 404):
                CACHE_DIR.mkdir(exist_ok=True)
                payload = r.json() if r.status_code == 200 else []
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                time.sleep(delay)
                return parse(payload)
            if r.status_code < 500 and r.status_code != 429:
                return None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2**attempt)
    return None


def parse(payload: list) -> dict[str, str]:
    """[{'HLS': {'url': ..., ...}, 'GND': {...}}] -> {'hls': url, 'gnd': url}"""
    links = {}
    for block in payload or []:
        for provider, info in block.items():
            if isinstance(info, dict) and info.get("url"):
                links[re.sub(r"[^a-z0-9]+", "_", provider.lower()).strip("_")] = info["url"]
    return links


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only process the first N persons (for testing)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.2, help="seconds each worker waits after a request")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    ap.add_argument("--offline", action="store_true", help="only use cached responses")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    arks = pl.read_csv(CSV_PATH).select("ark").unique().sort("ark")
    persons = pl.read_parquet(PERSONS_PATH).select(pl.col("id").alias("person_id"), "ark")
    todo = arks.join(persons, on="ark", how="left")
    if todo["person_id"].null_count():
        raise SystemExit(f"{todo['person_id'].null_count()} arks not found in persons_descriptors")
    if args.limit:
        todo = todo.head(args.limit)
    records = todo.sort("person_id").to_dicts()

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(args.workers) as ex:
        for i, links in enumerate(ex.map(lambda r: fetch(r["person_id"], args.refresh, args.offline, args.delay), records), 1):
            results.append(links)
            if i % 200 == 0 or i == len(records):
                print(f"{i}/{len(records)} ({time.time() - t0:.0f}s)")

    failed = [r["person_id"] for r, links in zip(records, results) if links is None]
    freq = Counter(p for links in results if links for p in links)
    providers = [p for p, _ in freq.most_common()]
    rows = [
        {"person_id": r["person_id"], "ark": r["ark"], "n_links": len(links or {}), **{p: (links or {}).get(p) for p in providers}}
        for r, links in zip(records, results)
    ]
    schema = {"person_id": pl.Int64, "ark": pl.String, "n_links": pl.Int64, **{p: pl.String for p in providers}}
    df = pl.DataFrame(rows, schema=schema)
    df.write_csv(args.out)

    print(f"\nWrote {args.out} ({df.height} rows)")
    print(f"persons with at least one link: {(df['n_links'] > 0).sum()} / {df.height}")
    print("providers (persons):", dict(freq.most_common()))
    if failed:
        print(f"WARNING {len(failed)} requests failed (not cached, re-run to retry): {failed[:10]}")


if __name__ == "__main__":
    main()

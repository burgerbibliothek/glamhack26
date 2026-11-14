"""Enrich the persons of birth_death_places_geo.csv with data scraped from their ARK landing pages.

Every `ark` in the CSV resolves (HTTP redirect) to one of two kinds of pages:

  * archives-quickaccess.ch/bbb/person/<id>        schema.org/Person markup (given/family name, dates, description)
  * katalog.burgerbib.ch/deskriptordetail.aspx?Id=<id>   catalogue "Deskriptor" page (Bezeichnung, Beschreibung, dates)

The result is written to a *separate* file, `person_enrichment.csv`, with one row per person and
`person_id` (= `persons_descriptors.id`, the numeric person descriptor id) as key, so it can be joined to
`birth_death_places_geo.csv` through `ark` (or to the parquet tables through `person_id`).

Raw HTML of successful responses is cached in `ark_cache/` (git-ignored), so the script is resumable and
parsing can be re-run offline.

Usage:
    python enrich_persons.py [--limit N] [--workers 3] [--delay 0.2] [--refresh] [--offline]
"""

from __future__ import annotations

import argparse
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import polars as pl
import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
CSV_PATH = HERE / "birth_death_places_geo.csv"
PERSONS_PATH = HERE / "persons_descriptors.parquet"
CACHE_DIR = HERE / "ark_cache"
OUT_PATH = HERE / "person_enrichment.csv"

USER_AGENT = "GLAMHack26-LivesInSpace/0.1 (hackathon data enrichment; github.com/burgerbibliothek/glamhack26)"

SCHEMA = {
    "person_id": pl.Int64,
    "ark": pl.String,
    "person_type": pl.String,  # natural | legal (from the descriptor's thesaurus path)
    "page_type": pl.String,  # quickaccess | katalog | None
    "page_url": pl.String,  # final URL after redirect
    "http_status": pl.Int64,
    "given_name": pl.String,
    "family_name": pl.String,
    "name": pl.String,  # 'LastName, FirstName' for reconciliation (e.g. OpenRefine), without life dates
    "birth_date": pl.String,  # as printed on the page, e.g. '12.11.1679' or '1752'
    "death_date": pl.String,
    "birth_date_iso": pl.String,  # YYYY-MM-DD or YYYY, None if not parseable
    "death_date_iso": pl.String,
    "birth_year": pl.Int64,
    "death_year": pl.Int64,
    "description": pl.String,
}


# --------------------------------------------------------------------------- fetching
_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
    return _local.s


def ark_key(ark: str) -> str:
    """'https://ark.burgerbib.ch/ark:36599/7rpc2cbm8p7' -> '7rpc2cbm8p7'"""
    return ark.rstrip("/").rsplit("/", 1)[-1]


def fetch(ark: str, refresh: bool, offline: bool, delay: float, retries: int = 3) -> tuple[int | None, str | None, str | None]:
    """Return (status, final_url, html). Successful pages are cached as <ark>.html with the URL on the first line."""
    # a few arks in the source data are the same URL twice, separated by a space: use the first one
    ark = ark.split()[0]
    path = CACHE_DIR / f"{ark_key(ark)}.html"
    if path.exists() and not refresh:
        url, _, html = path.read_text(encoding="utf-8").partition("\n")
        return 200, url, html
    if offline:
        return None, None, None
    status, url = None, None
    for attempt in range(retries):
        try:
            r = _session().get(ark, timeout=30)
            status, url = r.status_code, r.url
            if r.status_code == 200:
                CACHE_DIR.mkdir(exist_ok=True)
                path.write_text(f"{r.url}\n{r.text}", encoding="utf-8")
                time.sleep(delay)
                return 200, r.url, r.text
            if r.status_code < 500 and r.status_code != 429:
                break
        except requests.RequestException:
            pass
        time.sleep(2 ** attempt)
    return status, url, None


# --------------------------------------------------------------------------- parsing
def _text(el) -> str:
    """Text of an element; <br> becomes a newline, whitespace is normalised."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    lines = (re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in el.get_text().split("\n"))
    return "\n".join(ln for ln in lines if ln)


def parse_date(raw: str | None) -> tuple[str | None, int | None]:
    """'12.11.1679' -> ('1679-11-12', 1679); '1752' -> ('1752', 1752); '11.1679' -> ('1679-11', 1679); else (None, None)."""
    if not raw:
        return None, None
    raw = raw.strip()
    if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw):
        d, mo, y = map(int, m.groups())
        try:
            date(y, mo, d)
        except ValueError:
            return None, y
        return f"{y:04d}-{mo:02d}-{d:02d}", y
    if m := re.fullmatch(r"(\d{1,2})\.(\d{4})", raw):
        mo, y = map(int, m.groups())
        return (f"{y:04d}-{mo:02d}" if 1 <= mo <= 12 else None), y
    if re.fullmatch(r"\d{4}", raw):
        return raw, int(raw)
    return None, None


def parse_quickaccess(soup: BeautifulSoup) -> dict:
    sec = soup.select_one("section[itemtype*='schema.org/Person']")
    if sec is None:
        return {}
    prop = lambda name: (el.get_text(strip=True) if (el := sec.select_one(f"[itemprop={name}]")) else None) or None  # noqa: E731
    paras = []
    for p in sec.find_all("p"):
        if p.select_one("[itemprop]") or p.find("a", href=re.compile("katalog.burgerbib.ch")):
            continue  # date line / link to the catalogue
        if t := _text(p):
            paras.append(t)
    return {
        "given_name": prop("givenName"),
        "family_name": prop("familyName"),
        "birth_date": prop("birthDate"),
        "death_date": prop("deathDate"),
        "description": "\n".join(paras) or None,
    }


def parse_katalog(soup: BeautifulSoup) -> dict:
    fields = {}
    for lab in soup.select("td.veXDetailAttributLabel"):
        val = lab.find_next_sibling("td", class_="veXDetailAttributValue")
        if val is not None:
            fields.setdefault(lab.get_text(strip=True).rstrip(":"), _text(val) or None)
    return {
        "birth_date": fields.get("Geburtsdatum"),
        "death_date": fields.get("Todesdatum"),
        "description": fields.get("Beschreibung"),
    }


def parse_page(url: str | None, html: str | None) -> dict:
    if not html:
        return {}
    soup = BeautifulSoup(html, "lxml")
    if url and "archives-quickaccess.ch" in url:
        return {"page_type": "quickaccess", **parse_quickaccess(soup)}
    if url and "katalog.burgerbib.ch" in url:
        return {"page_type": "katalog", **parse_katalog(soup)}
    return {}


def descriptor_id_from_url(url: str | None) -> int | None:
    """Numeric descriptor id in the landing page URL (cross-check against persons_descriptors.id)."""
    if not url:
        return None
    u = urlparse(url)
    if m := re.search(r"/person/(\d+)", u.path):
        return int(m.group(1))
    q = {k.lower(): v for k, v in parse_qs(u.query).items()}
    return int(q["id"][0]) if "id" in q else None


# --------------------------------------------------------------------------- main
def person_type(id_name: str) -> str | None:
    if "Personen\\Natürliche Personen" in id_name:
        return "natural"
    if "Personen\\Juristische Personen" in id_name:
        return "legal"
    return None


def reconciliation_name(row: dict, label: str) -> str:
    """'LastName, FirstName' from the page's name fields; falls back to the descriptor label minus its '(dates)' suffix.

    The fallback covers katalog pages (no separate name fields) and legal entities (single name, no comma).
    """
    family, given = row.get("family_name"), row.get("given_name")
    if family and given:
        return f"{family}, {given}"
    return re.sub(r"\s*\([^()]*\)\s*$", "", label).strip()


def build_row(rec: dict, refresh: bool, offline: bool, delay: float) -> dict:
    status, url, html = fetch(rec["ark"], refresh, offline, delay)
    row = {"person_id": rec["person_id"], "ark": rec["ark"], "person_type": person_type(rec["id_name"]),
           "page_url": url, "http_status": status, **parse_page(url, html)}
    # the id in the URL must agree with the id from persons_descriptors, otherwise the join key is wrong
    page_id = descriptor_id_from_url(url)
    if page_id is not None and page_id != rec["person_id"]:
        print(f"WARNING id mismatch for {rec['ark']}: parquet={rec['person_id']} page={page_id}")
    row["name"] = reconciliation_name(row, rec["label"])
    for k in ("birth", "death"):
        row[f"{k}_date_iso"], row[f"{k}_year"] = parse_date(row.get(f"{k}_date"))
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only process the first N persons (for testing)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.2, help="seconds each worker waits after a request")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    ap.add_argument("--offline", action="store_true", help="only use cached pages")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    arks = pl.read_csv(CSV_PATH).select("ark").unique().sort("ark")
    persons = pl.read_parquet(PERSONS_PATH).select(pl.col("id").alias("person_id"), "id_name", "label", "ark")
    todo = arks.join(persons, on="ark", how="left")
    if todo["person_id"].null_count():
        raise SystemExit(f"{todo['person_id'].null_count()} arks not found in persons_descriptors")
    if args.limit:
        todo = todo.head(args.limit)
    records = todo.to_dicts()

    t0 = time.time()
    rows = []
    with ThreadPoolExecutor(args.workers) as ex:
        for i, row in enumerate(ex.map(lambda r: build_row(r, args.refresh, args.offline, args.delay), records), 1):
            rows.append(row)
            if i % 100 == 0 or i == len(records):
                print(f"{i}/{len(records)} ({time.time() - t0:.0f}s)")

    df = pl.DataFrame(rows, schema=SCHEMA).sort("person_id")
    df.write_csv(args.out)
    print(f"\nWrote {args.out} ({df.height} rows)")
    print("page_type:", dict(df["page_type"].value_counts(sort=True).iter_rows()))
    print("http_status:", dict(df["http_status"].value_counts(sort=True).iter_rows()))
    for c in ("description", "birth_date", "death_date", "birth_date_iso", "death_date_iso"):
        print(f"  {c}: {df[c].is_not_null().sum()} / {df.height}")


if __name__ == "__main__":
    main()

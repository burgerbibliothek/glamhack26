"""Build the JSON consumed by the map + arc visualisation (birth place -> death place).

Input : birth_death_places_geo.csv  (one row per person x place x role; role 1 = birth, 2 = death)
        places_descriptors.parquet  (place labels + hierarchy, used for country and sanity checks)
Output: map_data.json

    {
      "meta":   {...counts, filters, generation info...},
      "places": [{id, label, country, lat, lon, births, deaths, suspect}],   # points layer
      "arcs":   [{ark, name, birth_year, death_year, source, target,
                  from: [lon, lat], to: [lon, lat], distance_km, ambiguous}], # one arc per person
      "flows":  [{source, target, count, distance_km}]                        # aggregated arcs
    }

Usage:
    python build_map_json.py [--out PATH] [--keep-suspect]
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

HERE = Path(__file__).parent
CSV_PATH = HERE / "birth_death_places_geo.csv"
PLACES_PATH = HERE / "places_descriptors.parquet"
DEFAULT_OUT = HERE / "map_data.json"

BIRTH, DEATH = 1, 2

# Generous bounding box around Switzerland. Used to flag Swiss place descriptors that were
# geocoded to somewhere else (e.g. "Mullen (BE)" -> Nebraska).
CH_BBOX = {"lat": (45.7, 47.9), "lon": (5.9, 10.6)}

# Arcs shorter than this are considered "same place" (different descriptors, same town).
MIN_ARC_KM = 0.5

_PATH_RE = re.compile(r"(?<=\()Orte\\.+(?=\))")  # same pattern as data-wrangling-hierarchy.py


# --------------------------------------------------------------------------- helpers
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2) - math.radians(lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def parse_life_years(label: str) -> tuple[int | None, int | None]:
    """Extract (birth_year, death_year) from a person label like 'Name (1679-1767)'.

    Only unambiguous 4-digit years are returned. Partial dates ('19??'), 'belegt ...' (attested),
    centuries ('19. Jh.') and unknown ('?') give None. 'ca. 1732' and 'vor 1719' keep the year.
    """
    m = re.search(r"\(([^()]*)\)\s*$", label)
    if not m:
        return None, None
    dates = m.group(1)
    if dates.startswith("belegt") or "Jh" in dates:
        return None, None
    m = re.fullmatch(r"(?:ca\.\s*|vor\s*|nach\s*)?(\d{4}|\?|\d{1,3}\?+)\s*-\s*(?:ca\.\s*|vor\s*|nach\s*)?(\d{4}|\?|\d{1,3}\?+)?", dates)
    if not m:
        return None, None
    to_year = lambda s: int(s) if s and re.fullmatch(r"\d{4}", s) else None  # noqa: E731
    return to_year(m.group(1)), to_year(m.group(2))


def place_country(id_name: str, label: str) -> str | None:
    """Country descriptor of a place, from the hierarchy encoded in `id_name`.

    'Bern (BE) (Orte\\Sch\\Schweiz (CH)\\Bern (Kanton))' -> 'Schweiz (CH)'.
    Countries themselves ('Ägypten (ET) (Orte\\A)') are their own country.
    """
    m = _PATH_RE.search(id_name)
    if not m:
        return None
    parts = m.group().split("\\")
    return parts[2] if len(parts) >= 3 else label


def in_ch_bbox(lat: float, lon: float) -> bool:
    return CH_BBOX["lat"][0] <= lat <= CH_BBOX["lat"][1] and CH_BBOX["lon"][0] <= lon <= CH_BBOX["lon"][1]


# --------------------------------------------------------------------------- loading
def load_places(csv_path: Path = CSV_PATH, places_path: Path = PLACES_PATH) -> pl.DataFrame:
    """One row per place with coordinates, label, country and a `suspect` flag."""
    geo = pl.read_csv(csv_path)
    coords = geo.group_by("place_id").agg(pl.col("lat").first(), pl.col("long").first().alias("lon"))
    desc = pl.read_parquet(places_path).rename({"id": "place_id"})
    places = coords.join(desc, on="place_id", how="left").with_columns(
        pl.struct("id_name", "label")
        .map_elements(lambda s: place_country(s["id_name"], s["label"]), return_dtype=pl.String)
        .alias("country")
    )
    # Swiss descriptor but coordinates outside Switzerland => geocoding error.
    return places.with_columns(
        (
            pl.col("country").eq("Schweiz (CH)")
            & ~(
                pl.col("lat").is_between(*CH_BBOX["lat"]) & pl.col("lon").is_between(*CH_BBOX["lon"])
            )
        ).alias("suspect")
    ).sort("place_id")


def load_geo(csv_path: Path = CSV_PATH) -> pl.DataFrame:
    return pl.read_csv(csv_path).unique().sort("ark", "role", "place_id")


# --------------------------------------------------------------------------- building
def build(keep_suspect: bool = False) -> dict:
    geo = load_geo()
    places = load_places()
    place = {r["place_id"]: r for r in places.iter_rows(named=True)}
    suspect_ids = {pid for pid, r in place.items() if r["suspect"]}

    # place point layer: counts over *all* persons (also those that cannot be drawn as an arc)
    counts = geo.group_by("place_id").agg(
        (pl.col("role") == BIRTH).sum().alias("births"), (pl.col("role") == DEATH).sum().alias("deaths")
    )
    count = {r["place_id"]: (r["births"], r["deaths"]) for r in counts.iter_rows(named=True)}
    places_out = [
        {
            "id": pid,
            "label": r["label"],
            "country": r["country"],
            "lat": round(r["lat"], 5),
            "lon": round(r["lon"], 5),
            "births": count[pid][0],
            "deaths": count[pid][1],
            "suspect": r["suspect"],
        }
        for pid, r in place.items()
        if keep_suspect or not r["suspect"]
    ]

    # arcs: for each person, birth place(s) x death place(s)
    stats = {"persons": 0, "persons_with_arc_candidate": 0, "skipped_suspect": 0, "skipped_same_place": 0}
    arcs = []
    for ark, grp in geo.group_by("ark", maintain_order=True):
        stats["persons"] += 1
        rows = grp.to_dicts()
        births = [r for r in rows if r["role"] == BIRTH]
        deaths = [r for r in rows if r["role"] == DEATH]
        if not births or not deaths:
            continue
        stats["persons_with_arc_candidate"] += 1
        name = rows[0]["person_label"]
        by, dy = parse_life_years(name)
        ambiguous = len(births) > 1 or len(deaths) > 1
        for b in births:
            for d in deaths:
                if not keep_suspect and (b["place_id"] in suspect_ids or d["place_id"] in suspect_ids):
                    stats["skipped_suspect"] += 1
                    continue
                dist = haversine_km(b["lat"], b["long"], d["lat"], d["long"])
                if dist < MIN_ARC_KM:
                    stats["skipped_same_place"] += 1
                    continue
                arcs.append(
                    {
                        "ark": ark[0],
                        "name": name,
                        "birth_year": by,
                        "death_year": dy,
                        "source": b["place_id"],
                        "target": d["place_id"],
                        "from": [round(b["long"], 5), round(b["lat"], 5)],
                        "to": [round(d["long"], 5), round(d["lat"], 5)],
                        "distance_km": round(dist, 1),
                        "ambiguous": ambiguous,
                    }
                )

    # aggregated flows (origin -> destination), for a lighter default view
    flows: dict[tuple[int, int], dict] = {}
    for a in arcs:
        f = flows.setdefault(
            (a["source"], a["target"]),
            {"source": a["source"], "target": a["target"], "count": 0, "distance_km": a["distance_km"]},
        )
        f["count"] += 1
    flows_out = sorted(flows.values(), key=lambda f: -f["count"])

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": CSV_PATH.name,
        "keep_suspect": keep_suspect,
        "n_places": len(places_out),
        "n_suspect_places": len(suspect_ids),
        "n_arcs": len(arcs),
        "n_flows": len(flows_out),
        "min_arc_km": MIN_ARC_KM,
        **stats,
    }
    return {"meta": meta, "places": places_out, "arcs": arcs, "flows": flows_out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep-suspect", action="store_true", help="keep places/arcs with implausible coordinates")
    args = ap.parse_args()

    data = build(keep_suspect=args.keep_suspect)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size / 1024:.0f} kB)")
    for k, v in data["meta"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

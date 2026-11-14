"""
Script to fetch data over the Nominatim API.
"""
import json
import re
import time

import polars as pl
import requests

# Mapping the codes to ISO-3166
mapping_country_codes = {
    "(A)": "at",
    "(AFG)": "af",
    "(AL)": "al",
    "(ARG)": "ar",
    "(ARM)": "am",
    "(AUS)": "au",
    "(B)": "be",
    "(BDI)": "bi",
    "(BGR)": "bg",
    "(BIH)": "ba",
    "(BJ)": "bj",
    "(BLR)": "by",
    "(BOL)": "bo",
    "(BR)": "br",
    "(CAN)": "ca",
    "(CH)": "ch",
    "(CHL)": "cl",
    "(CHN)": "cn",
    "(CMR)": "cm",
    "(CO)": "co",
    "(COD)": "cd",
    "(COG)": "cg",
    "(CZ)": "cz",
    "(D)": "de",
    "(DDR)": "dd",
    "(DK)": "dk",
    "(DZ)": "dz",
    "(E)": "es",
    "(ER)": "er",
    "(ES)": "sv",
    "(EST)": "ee",
    "(ET)": "eg",
    "(ETH)": "et",
    "(F)": "fr",
    "(FL)": "li",
    "(FP)": "pf",
    "(GAB)": "ga",
    "(GB)": "gb",
    "(GEO)": "ge",
    "(GHA)": "gh",
    "(GRC)": "gr",
    "(GRL)": "gl",
    "(GUI)": "gn",
    "(H)": "hu",
    "(HR)": "hr",
    "(HT)": "ht",
    "(I)": "it",
    "(IDN)": "id",
    "(IL)": "il",
    "(IND)": "in",
    "(IR)": "ir",
    "(IRL)": "ie",
    "(IRQ)": "iq",
    "(ISL)": "is",
    "(J)": "jp",
    "(JA)": "jm",
    "(JOR)": "jo",
    "(K)": "kh",
    "(KEN)": "ke",
    "(KOR)": "kr",
    "(L)": "lu",
    "(LAO)": "la",
    "(LBN)": "lb",
    "(LBR)": "lr",
    "(LBY)": "ly",
    "(LIT)": "lt",
    "(LK)": "lk",
    "(LS)": "ls",
    "(LV)": "lv",
    "(M)": "mt",
    "(MA)": "ma",
    "(MAL)": "my",
    "(MC)": "mc",
    "(MEX)": "mx",
    "(MG)": "mg",
    "(MK)": "mk",
    "(MNE)": "me",
    "(MOZ)": "mz",
    "(N)": "no",
    "(NAM)": "na",
    "(NEP)": "np",
    "(NGR)": "ng",
    "(NIC)": "ni",
    "(NL)": "nl",
    "(NZ)": "nz",
    "(P)": "pt",
    "(PE)": "pe",
    "(PHL)": "ph",
    "(PK)": "pk",
    "(PL)": "pl",
    "(PRI)": "pr",
    "(PY)": "py",
    "(RKS)": "xk",
    "(RN)": "ne",
    "(RO)": "ro",
    "(RUS)": "ru",
    "(RWA)": "rw",
    "(S)": "se",
    "(SAU)": "sa",
    "(SDN)": "sd",
    "(SF)": "fi",
    "(SK)": "sk",
    "(SLO)": "si",
    "(SN)": "sn",
    "(SRB)": "rs",
    "(SSD)": "ss",
    "(SUR)": "sr",
    "(SWZ)": "sz",
    "(SYR)": "sy",
    "(TD)": "td",
    "(TG)": "to",
    "(THA)": "th",
    "(TN)": "tn",
    "(TR)": "tr",
    "(TZA)": "tz",
    "(UGA)": "ug",
    "(UKR)": "ua",
    "(USA)": "us",
    "(UY)": "uy",
    "(V)": "va",
    "(VEN)": "ve",
    "(VN)": "vn",
    "(WS)": "ws",
    "(YEM)": "ye",
    "(ZA)": "za",
    "(ZW)": "zw",
}

# Create Dataframe with labels and country codes in separate columns
df = pl.DataFrame(schema={"id": pl.Int64, "label": pl.String, "country_codes": pl.String})

places = pl.read_csv("places_tr.csv")

for p in places.rows(named=True):
    match = re.search("\\([A-z]{1,3}\\)", p["label"])
    p["label"] = re.sub("\\([A-z]{1,3}\\)", "", p["label"])
    if match is not None:
        match = [mapping_country_codes.get(match[0])]
    else:
    	match = [""]

    append_df = pl.DataFrame({"id": p["id"], "label" : p["label"], "country_codes" : match[0]})
    df = pl.concat([df, append_df])

# Fetch geo coordinates with Nominatim
BASE = "https://nominatim.openstreetmap.org/search"
UA = "GLAMHack26/1.0 (giodi@proton.me)"

CACHE = {}  # {...: result} — cache results, policy asks for it
_last = 0.0


def nominatim_search(
    query: str,
    country_code: str | None = None,
    limit: int = 1,
    addressdetails: bool = True,
) -> list[dict]:
    """Free-form search; country_code is an ISO 3166-1 alpha-2 code (hard filter)."""
    global _last
    params = {
        "q": query,
        "format": "jsonv2",  # explicit: default on the public server is the web UI
        "limit": min(limit, 40),
        "addressdetails": int(addressdetails),
    }

    key = tuple(sorted(params.items()))
    if key in CACHE:
        return CACHE[key]

    wait = 1.0 - (time.monotonic() - _last)  # max 1 req/s
    if wait > 0:
        time.sleep(wait)
    try:
        r = requests.get(
            BASE,
            params=params,
            headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.8"},
            timeout=10,
        )
        r.raise_for_status()
        _last = time.monotonic()
        data = r.json()
    finally:
        _last = time.monotonic()

    CACHE[key] = data
    return data

# Iiterate places an try to fetch geo data, then save the result as JSON in the osm folder
for place in df.rows(named=True):
	print(place)
	nom_data = nominatim_search(place["label"], country_code=place["country_codes"], limit=1)
	file = open(f'osm/{place['id']}.json', 'w')
	json.dump(nom_data, file, ensure_ascii=False, indent=4)
	file.close()

import json
from pathlib import Path

import polars as pl

df = pl.read_csv("birth_death_places.csv")
df_geo = pl.DataFrame(strict=False)
for p in df.rows(named=True):
	try:
		with open(f"osm/{p['place_id']}.json", 'r', encoding='utf-8') as file:
		    data = json.load(file)

		lat = float(data[0]['lat'])
		lon = float(data[0]['lon'])

		append_df = pl.DataFrame({"place_id": p['place_id'], "role": p['role'], "ark": p['ark'], "person_label": p['person_label'], "lat" : lat, "long" : lon})
		df_geo = pl.concat([df_geo, append_df])

	except: 
		("not found")

df_geo.write_csv("birth_death_places_geo.csv")
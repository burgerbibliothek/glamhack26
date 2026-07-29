import polars as pl
from pathlib import Path

p = Path(__file__).parent

# Export list containing only IDs of descriptors of the type "Orte"
df = pl.read_parquet(p / "export_ve_desk.parquet")
get_places_ids = df.sql("SELECT vrzng_enht_id, dskrp_id, ve_gsft_obj_kurz_nm, gsft_obj_bzhng_rolle_nm, thsrs_nm FROM self WHERE thsrs_nm = 'Orte' ORDER BY dskrp_id")
get_places_ids.write_parquet("./export_ve_desk_orte.parquet")
print('Saved export_desk_orte.parquet')

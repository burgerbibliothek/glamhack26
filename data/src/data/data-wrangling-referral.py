import polars as pl
from pathlib import Path
import re

# Create referral table
df_place_descriptors = pl.read_csv("place_descriptors.csv", separator=";", encoding="utf-16-le").with_columns(pl.col("Siehe unter").str.split(";")).explode("Siehe unter").with_columns(pl.col("Siehe unter").str.strip_chars())
df_referral = pl.DataFrame(schema = {"id_main_descriptor" : pl.Int64, "id_referral_descriptor" : pl.Int64})

for row in df_place_descriptors.rows(named=True):
    if row["Siehe unter"] is not None:
        id_nr = df_place_descriptors.sql(f"""
            SELECT `ID-Nr.` 
            FROM self
            WHERE Bezeichnung = '{row["Siehe unter"].replace("'", "''")}'
        """)

        if not id_nr.is_empty():
            id_nr = id_nr.row(0)[0]
            append_df = pl.DataFrame({"id_main_descriptor" : id_nr, "id_referral_descriptor" : row["ID-Nr."]})
            df_referral = pl.concat([df_referral, append_df])

print(df_referral)
df_referral.write_parquet("main_places_descriptors_referral_places_descriptors.parquet")

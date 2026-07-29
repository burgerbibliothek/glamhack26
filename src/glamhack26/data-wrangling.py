import polars as pl
from pathlib import Path
import re

p = Path(__file__).parent

# Create table w/ hierarchy
df_orte = pl.read_parquet(p / "export_desk_orte.parquet")
df_hierarchy = pl.DataFrame(schema={"id_descriptor_child": pl.Int64, "id_descriptor_parent": pl.Int64})

pattern = re.compile(r"(?<=\()Orte\\.+(?=\))")
for row in df_orte.rows(named=True):
    match = re.search(pattern, row['id_name_descriptor'])
    if match is not None:
        name = match.group().split("\\")
        id_child = row['id_descriptor']
        parent_id_name = re.sub(r"'", "''", f'{name[-1]} ({"\\".join(name[:-1])})')
        parent_df = df_orte.sql(f"SELECT id_descriptor FROM self WHERE id_name_descriptor = '{parent_id_name}'")
        
        if not parent_df.is_empty():
            id_parent = parent_df.row()[0]
            append_df = pl.DataFrame({"id_descriptor_child" : id_child, "id_descriptor_parent" : id_parent})                             # new DataFrame with the new rows
            df_hierarchy = pl.concat([df_hierarchy, append_df])
    
df_hierarchy.write_parquet(p / "hierarchy.parquet")

        



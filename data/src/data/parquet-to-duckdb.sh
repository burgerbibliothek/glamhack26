for f in *.parquet; do
  echo "$f"
  t=$(basename "$f" .parquet)
  duckdb glamhack26.duckdb -c "CREATE OR REPLACE TABLE $t AS SELECT * FROM read_parquet('$f');"
done
duckdb glamhack26.duckdb -c "SHOW TABLES; SELECT * FROM places_descriptors LIMIT 5;"

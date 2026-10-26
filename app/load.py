"""Ładowanie powiatu i siatek H3 do bieżącego środowiska."""
import json
import os
import subprocess
from pathlib import Path
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values
from python.h3_generator import generate_grid, read_area


def main():
    path = Path(os.getenv("POWIAT_FILE", "/data/powiat.shp"))
    if not path.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku: {path}")
    resolutions = sorted(set(int(v) for v in os.getenv("H3_RESOLUTIONS", "7,8,9").split(",")))
    area = read_area(path)
    grids = {res: list(generate_grid(area, res)) for res in resolutions}
    # Libpq i ogr2ogr korzystają z PGHOST, PGPORT, PGDATABASE, PGUSER i PGPASSWORD.
    subprocess.run(["ogr2ogr", "-f", "PostgreSQL", "PG:", str(path),
                    "-nln", "public.powiat", "-lco", "GEOMETRY_NAME=geom", "-overwrite"], check=True)
    with psycopg2.connect(connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS grid")
            for res, features in grids.items():
                table = sql.Identifier("grid", f"h3_r{res}")
                cursor.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (h3_id text PRIMARY KEY, "
                                       "resolution smallint NOT NULL, geom geometry(Polygon,4326) NOT NULL)").format(table))
                cursor.execute(sql.SQL("DELETE FROM {}").format(table))
                records = [(f["properties"]["h3_id"], res, json.dumps(f["geometry"])) for f in features]
                execute_values(cursor, sql.SQL("INSERT INTO {} (h3_id, resolution, geom) VALUES %s").format(table).as_string(connection),
                               records, template="(%s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))", page_size=1000)
                cursor.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING gist (geom)").format(
                    sql.Identifier(f"h3_r{res}_geom_idx"), table))
                print(f"grid.h3_r{res}: {len(features)} komórek")
    print("Załadowano powiat i siatki H3.")


if __name__ == "__main__":
    main()

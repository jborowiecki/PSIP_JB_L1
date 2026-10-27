"""BDOT10K: import i analiza w EPSG:2180, wyniki w EPSG:3857."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import zipfile

import h3
from osgeo import ogr
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

SOURCE_URL = 'https://opendata.geoportal.gov.pl/bdot10k/schemat2021/GPKG/14/1428_GPKG.zip'
STAGES = {'bdot': '_lab2_bdot', 'analiza': '_lab2_analiza', 'wyniki': '_lab2_wyniki'}


def identifier(*parts):
    return sql.Identifier(*parts)


def query(connection, statement, params=None, fetch=False):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall() if fetch else None


def prepare_archive(cache, refresh=False):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / '1428_GPKG.zip'
    if refresh or not archive.exists():
        pending = archive.with_suffix('.download')
        request = urllib.request.Request(SOURCE_URL, headers={'User-Agent': 'PSIP-Lab2/1.0'})
        with urllib.request.urlopen(request, timeout=120) as response, pending.open('wb') as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
        with zipfile.ZipFile(pending) as package:
            if package.testzip() is not None:
                raise ValueError('Uszkodzona paczka BDOT10K.')
        pending.replace(archive)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    with zipfile.ZipFile(archive) as package:
        files = sorted(name for name in package.namelist() if name.lower().endswith('.gpkg'))
    if not files:
        raise ValueError('Paczka nie zawiera plików GeoPackage.')
    return archive, digest, files


def prepare_schemas(connection):
    if not query(connection, "SELECT pg_try_advisory_lock(hashtext('psip_lab2_bdot'))", fetch=True)[0][0]:
        raise RuntimeError('Inny proces wykonuje już analizę BDOT10K.')
    for target, stage in STAGES.items():
        exists = query(connection, 'SELECT to_regnamespace(%s)', (target,), True)[0][0]
        if exists and not query(connection, "SELECT to_regclass('wyniki.zrodlo') IS NOT NULL", fetch=True)[0][0]:
            raise RuntimeError(f'Schemat {target} już istnieje i nie jest wynikiem tego skryptu.')
        query(connection, sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(identifier(stage)))
        query(connection, sql.SQL('CREATE SCHEMA {}').format(identifier(stage)))
    query(connection, """
        CREATE TABLE _lab2_analiza.powiat AS
        SELECT ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_UnaryUnion(ST_Collect(
            ST_Force2D(ST_Transform(geom,2180))))),3))::geometry(MultiPolygon,2180) AS geom
        FROM public.powiat
    """)
    area, valid = query(connection, 'SELECT ST_Area(geom), ST_IsValid(geom) FROM _lab2_analiza.powiat', fetch=True)[0]
    if not area or not valid:
        raise ValueError('Niepoprawna lub pusta granica powiatu.')
    print(f'Powiat w EPSG:2180: {area:.3f} m2', flush=True)
    query(connection, """
        CREATE TABLE _lab2_wyniki.zrodlo (
            url text, sha256 text, pobrano timestamptz DEFAULT now(),
            powiat_teryt text DEFAULT '1428', epsg_obliczen integer DEFAULT 2180,
            epsg_wynikow integer DEFAULT 3857, h3_version text)
    """)
    query(connection, """
        CREATE TABLE _lab2_wyniki.warstwy (
            warstwa text PRIMARY KEY, plik text, wymiar integer, kolumna text,
            jednostka text, liczba_zrodlowa bigint, liczba_w_powiecie bigint,
            naprawione_geometrie bigint, suma_zrodlowa double precision)
    """)
    query(connection, """
        CREATE TABLE _lab2_wyniki.kontrola (
            resolution integer, warstwa text, suma_zrodlowa double precision,
            suma_w_siatce double precision, roznica double precision,
            tolerancja double precision, poprawne boolean,
            PRIMARY KEY (resolution, warstwa))
    """)


def create_grids(connection, resolutions):
    # Wyłącznie interfejs indeksowania H3 wymaga WGS84. Wszystkie operacje
    # geometrii, przycinanie i pomiary poniżej wykonywane są w EPSG:2180.
    geo = query(connection, 'SELECT ST_AsGeoJSON(ST_Transform(ST_Segmentize(geom,50),4326)) FROM _lab2_analiza.powiat', fetch=True)[0][0]
    shape = h3.geo_to_h3shape(json.loads(geo))
    for res in resolutions:
        table = identifier('_lab2_analiza', f'h3_r{res}')
        cells = sorted(h3.h3shape_to_cells_experimental(shape, res, contain='bbox_overlap'))
        records = []
        for cell in cells:
            ring = [[lon, lat] for lat, lon in h3.cell_to_boundary(cell)]
            ring.append(ring[0])
            records.append((cell, res, json.dumps({'type': 'Polygon', 'coordinates': [ring]})))
        query(connection, sql.SQL('CREATE TABLE {} (h3_id text PRIMARY KEY, resolution smallint, cell_area_m2 double precision, full_geom geometry(MultiPolygon,2180), geom geometry(MultiPolygon,2180))').format(table))
        with connection.cursor() as cursor:
            execute_values(cursor, sql.SQL('INSERT INTO {} (h3_id,resolution,geom) VALUES %s').format(table).as_string(connection), records,
                           template='(%s,%s,ST_Multi(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),2180)))', page_size=1000)
        query(connection, sql.SQL('UPDATE {} SET full_geom=geom').format(table))
        query(connection, sql.SQL('DELETE FROM {} g USING _lab2_analiza.powiat p WHERE NOT ST_Intersects(g.geom,p.geom)').format(table))
        query(connection, sql.SQL('UPDATE {} g SET geom=ST_Multi(ST_CollectionExtract(ST_Intersection(g.geom,p.geom),3)) FROM _lab2_analiza.powiat p').format(table))
        query(connection, sql.SQL('DELETE FROM {} WHERE ST_IsEmpty(geom) OR ST_Area(geom)=0').format(table))
        query(connection, sql.SQL('UPDATE {} SET cell_area_m2=ST_Area(geom)').format(table))
        query(connection, sql.SQL('CREATE INDEX ON {} USING gist(geom)').format(table))
        query(connection, sql.SQL('CREATE INDEX ON {} USING gist(full_geom)').format(table))
        query(connection, sql.SQL('ANALYZE {}').format(table))
        missing, overlap, count = query(connection, sql.SQL('''
            SELECT ST_Area(ST_Difference(p.geom,g.geom)),
                   g.sum_area-ST_Area(g.geom),g.n
            FROM _lab2_analiza.powiat p CROSS JOIN
            (SELECT ST_UnaryUnion(ST_Collect(geom)) geom,SUM(cell_area_m2) sum_area,COUNT(*) n FROM {}) g
        ''').format(table), fetch=True)[0]
        if missing > 1 or abs(overlap) > 1:
            raise ValueError(f'Siatka {res}: brakujące pokrycie {missing} m2, nakładanie {overlap} m2.')
        print(f'Siatka 2180 / r{res}: {count} komórek, brakujące pokrycie {missing:.6f} m2', flush=True)


def import_layers(connection, archive, files):
    layers = []
    for member in files:
        path = f'/vsizip/{archive}/{member}'
        source = ogr.Open(path)
        if source is None:
            raise ValueError(f'Nie można odczytać {member}')
        for layer in source:
            name = layer.GetName().lower()
            if not re.fullmatch(r'ot_[a-z0-9]+_[alp]', name):
                raise ValueError(f'Nieoczekiwana nazwa warstwy: {name}')
            reference = layer.GetSpatialRef()
            if reference is None:
                raise ValueError(f'Warstwa {name} nie ma CRS.')
            flattened = ogr.GT_Flatten(layer.GetGeomType())
            dimensions = {ogr.wkbPoint: 0, ogr.wkbMultiPoint: 0,
                          ogr.wkbLineString: 1, ogr.wkbMultiLineString: 1,
                          ogr.wkbPolygon: 2, ogr.wkbMultiPolygon: 2}
            if flattened not in dimensions:
                raise ValueError(f'Nieobsługiwany typ geometrii {flattened}: {name}')
            dim = dimensions[flattened]
            table = identifier('_lab2_bdot', name)
            original_count = layer.GetFeatureCount()
            subprocess.run(['ogr2ogr', '--config', 'PG_USE_COPY', 'YES', '-f', 'PostgreSQL',
                            'PG:', path, layer.GetName(), '-nln', f'_lab2_bdot.{name}',
                            '-t_srs', 'EPSG:2180', '-dim', 'XY', '-nlt', 'PROMOTE_TO_MULTI',
                            '-lco', 'GEOMETRY_NAME=geom', '-lco', 'FID=feature_id', '-overwrite'], check=True)
            bad = query(connection, sql.SQL('SELECT COUNT(*) FROM {} WHERE geom IS NOT NULL AND NOT ST_IsValid(geom)').format(table), fetch=True)[0][0]
            if bad:
                query(connection, sql.SQL('UPDATE {} SET geom=ST_Multi(ST_CollectionExtract(ST_MakeValid(geom),%s)) WHERE NOT ST_IsValid(geom)').format(table), (dim + 1,))
            query(connection, sql.SQL('DELETE FROM {} b WHERE geom IS NULL OR ST_IsEmpty(geom) OR NOT EXISTS (SELECT 1 FROM _lab2_analiza.powiat p WHERE ST_Intersects(b.geom,p.geom))').format(table))
            query(connection, sql.SQL('UPDATE {} b SET geom=ST_Multi(ST_CollectionExtract(ST_Intersection(b.geom,p.geom),%s)) FROM _lab2_analiza.powiat p WHERE NOT ST_Covers(p.geom,b.geom)').format(table), (dim + 1,))
            query(connection, sql.SQL('DELETE FROM {} WHERE ST_IsEmpty(geom)').format(table))
            expression = ['ST_NumGeometries(geom)', 'ST_Length(geom)', 'ST_Area(geom)'][dim]
            count, total, invalid = query(connection, sql.SQL('SELECT COUNT(*),COALESCE(SUM({}),0),COUNT(*) FILTER(WHERE NOT ST_IsValid(geom) OR ST_SRID(geom)<>2180) FROM {}').format(sql.SQL(expression), table), fetch=True)[0]
            if invalid:
                raise ValueError(f'Warstwa {name}: niepoprawne geometrie po przycięciu.')
            query(connection, sql.SQL('ANALYZE {}').format(table))
            unit = ['n', 'm', 'm2'][dim]
            column = f'{name}_{unit}'
            query(connection, 'INSERT INTO _lab2_wyniki.warstwy VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                  (name, member, dim, column, unit, original_count, count, bad, total))
            layers.append((name, dim, column, float(total)))
            print(f'BDOT {name}: {count}/{original_count} obiektów w powiecie, EPSG:2180', flush=True)
    return layers


def aggregate_layer(connection, res, name, dim, column, expected):
    grid = identifier('_lab2_analiza', f'h3_r{res}')
    bdot = identifier('_lab2_bdot', name)
    col = identifier(column)
    query(connection, sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {} NOT NULL DEFAULT 0').format(grid, col, sql.SQL('bigint' if dim == 0 else 'double precision')))
    query(connection, sql.SQL('UPDATE {} SET {}=0').format(grid,col))
    if dim == 0:
        aggregation = sql.SQL('''
            WITH assigned AS (
                SELECT DISTINCT ON (b.feature_id,p.path) g.h3_id,b.feature_id,p.path
                FROM {bdot} b CROSS JOIN LATERAL ST_Dump(b.geom) p
                JOIN {grid} g ON g.full_geom && p.geom AND ST_Covers(g.full_geom,p.geom)
                ORDER BY b.feature_id,p.path,g.h3_id)
            SELECT h3_id,COUNT(*) value FROM assigned GROUP BY h3_id
        ''').format(bdot=bdot, grid=grid)
    elif dim == 1:
        # Wspólne odcinki leżące dokładnie na granicy komórek są przypisane
        # tylko raz, do najmniejszego h3_id spośród przecinanych komórek.
        aggregation = sql.SQL('''
            WITH pieces AS MATERIALIZED (
                SELECT b.feature_id,s.path segment_path,g.h3_id,
                       CASE WHEN ST_Covers(g.full_geom,s.geom) THEN s.geom ELSE
                           ST_CollectionExtract(ST_Intersection(s.geom,g.full_geom),2) END part
                FROM {bdot} b CROSS JOIN LATERAL ST_DumpSegments(b.geom) s
                JOIN {grid} g ON s.geom && g.full_geom AND ST_Intersects(s.geom,g.full_geom)),
            ordered AS (
                SELECT *, ST_Collect(part) OVER(PARTITION BY feature_id,segment_path ORDER BY h3_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) previous
                FROM pieces WHERE NOT ST_IsEmpty(part))
            SELECT h3_id,SUM(ST_Length(CASE WHEN previous IS NULL THEN part
                ELSE ST_Difference(part,ST_UnaryUnion(previous)) END)) value
            FROM ordered GROUP BY h3_id
        ''').format(bdot=bdot, grid=grid)
    else:
        aggregation = sql.SQL('''
            SELECT g.h3_id,SUM(ST_Area(CASE WHEN ST_Covers(g.full_geom,b.geom) THEN b.geom ELSE
                ST_CollectionExtract(ST_Intersection(b.geom,g.full_geom),3) END)) value
            FROM {bdot} b JOIN {grid} g ON b.geom && g.full_geom AND ST_Intersects(b.geom,g.full_geom)
            GROUP BY g.h3_id
        ''').format(bdot=bdot, grid=grid)
    query(connection, sql.SQL('UPDATE {grid} g SET {col}=a.value FROM ({aggregation}) a WHERE g.h3_id=a.h3_id').format(grid=grid, col=col, aggregation=aggregation))
    actual = float(query(connection, sql.SQL('SELECT SUM({}) FROM {}').format(col, grid), fetch=True)[0][0])
    tolerance = 0 if dim == 0 else max(0.01, abs(expected) * 1e-8)
    difference = actual - expected
    valid = abs(difference) <= tolerance
    query(connection, '''INSERT INTO _lab2_wyniki.kontrola VALUES (%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(resolution,warstwa) DO UPDATE SET suma_zrodlowa=EXCLUDED.suma_zrodlowa,
          suma_w_siatce=EXCLUDED.suma_w_siatce,roznica=EXCLUDED.roznica,
          tolerancja=EXCLUDED.tolerancja,poprawne=EXCLUDED.poprawne''',
          (res, name, expected, actual, difference, tolerance, valid))
    if not valid:
        raise ValueError(f'Bilans {name}/r{res}: {actual} zamiast {expected}; różnica {difference}.')


def export_and_publish(connection, resolutions, digest):
    for res in resolutions:
        columns = query(connection, "SELECT column_name FROM information_schema.columns WHERE table_schema='_lab2_analiza' AND table_name=%s AND column_name NOT IN ('geom','full_geom') ORDER BY ordinal_position", (f'h3_r{res}',), True)
        query(connection, sql.SQL('CREATE TABLE {} AS SELECT {},ST_Transform(geom,3857)::geometry(MultiPolygon,3857) geom FROM {}').format(
            identifier('_lab2_wyniki', f'h3_r{res}'), sql.SQL(',').join(identifier(row[0]) for row in columns), identifier('_lab2_analiza', f'h3_r{res}')))
        query(connection, sql.SQL('ALTER TABLE {} ADD PRIMARY KEY(h3_id)').format(identifier('_lab2_wyniki', f'h3_r{res}')))
        query(connection, sql.SQL('CREATE INDEX ON {} USING gist(geom)').format(identifier('_lab2_wyniki', f'h3_r{res}')))
        invalid = query(connection, sql.SQL('SELECT COUNT(*) FILTER(WHERE geom IS NULL OR ST_IsEmpty(geom) OR NOT ST_IsValid(geom) OR ST_SRID(geom)<>3857) FROM {}').format(identifier('_lab2_wyniki',f'h3_r{res}')),fetch=True)[0][0]
        if invalid:
            raise ValueError(f'Niepoprawne geometrie wynikowe r{res}: {invalid}')
    query(connection, 'CREATE TABLE _lab2_wyniki.powiat AS SELECT ST_Transform(geom,3857)::geometry(MultiPolygon,3857) geom FROM _lab2_analiza.powiat')
    query(connection, 'INSERT INTO _lab2_wyniki.zrodlo(url,sha256,h3_version) VALUES (%s,%s,%s)', (SOURCE_URL, digest, h3.__version__))
    count, valid = query(connection,'SELECT COUNT(*),bool_and(poprawne) FROM _lab2_wyniki.kontrola',fetch=True)[0]
    layer_count = query(connection,'SELECT COUNT(*) FROM _lab2_wyniki.warstwy',fetch=True)[0][0]
    if count != len(resolutions)*layer_count or not valid:
        raise ValueError('Niekompletne lub niepoprawne kontrole bilansów.')
    with connection:
        for target, stage in STAGES.items():
            query(connection, sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(identifier(target)))
            query(connection, sql.SQL('ALTER SCHEMA {} RENAME TO {}').format(identifier(stage), identifier(target)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', default=os.getenv('BDOT_CACHE', '/bdot'))
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--resolutions', nargs='+', type=int, default=[7,8,9])
    args = parser.parse_args()
    if os.getenv('ENVIRONMENT') != 'env-test':
        raise RuntimeError('Import BDOT10K jest przeznaczony wyłącznie dla ENVIRONMENT=env-test.')
    if any(res < 0 or res > 15 for res in args.resolutions):
        raise ValueError('Rozdzielczości H3 muszą być z zakresu 0–15.')
    resolutions = sorted(set(args.resolutions))
    started = time.monotonic()
    archive, digest, files = prepare_archive(Path(args.cache_dir), args.refresh)
    print(f'Źródło: {SOURCE_URL}\nSHA256: {digest}\nPlików GPKG: {len(files)}', flush=True)
    connection = psycopg2.connect(connect_timeout=15)
    try:
        connection.autocommit = True
        prepare_schemas(connection)
        create_grids(connection, resolutions)
        layers = import_layers(connection, archive, files)
        for res in resolutions:
            for index, (name, dim, column, expected) in enumerate(layers, 1):
                aggregate_layer(connection, res, name, dim, column, expected)
                print(f'Agregacja r{res}: {index}/{len(layers)} {column}', flush=True)
        export_and_publish(connection, resolutions, digest)
        query(connection, "SELECT pg_advisory_unlock(hashtext('psip_lab2_bdot'))")
    finally:
        connection.close()
    print(f'Gotowe: bdot i analiza w EPSG:2180; wyniki w EPSG:3857. Czas: {time.monotonic()-started:.1f}s', flush=True)


if __name__ == '__main__':
    main()

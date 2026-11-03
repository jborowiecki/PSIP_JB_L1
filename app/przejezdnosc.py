"""Analiza wielokryterialna BDOT10K: obliczenia 2180, wynik 3857."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, RealDictCursor, execute_values

from python.passability_model import calculate, sensitivity
from verify_bdot import verify as verify_bdot

WORK = 'przejezdnosc_robocza'
RESULT = 'przejezdnosc'
MARKER = 'psip_passability_v1'
DEFAULT_CONFIG = Path(__file__).parent / 'config/przejezdnosc_wagi.json'


def q(connection, statement, params=None):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall() if cursor.description else None


def ident(*parts):
    return sql.Identifier(*parts)


def validate_config(config, layers):
    if config['denominator'] != 'all_present_layers_including_zero_weights':
        raise ValueError('Nieobsługiwany mianownik indeksu.')
    if config['normalization'] != 'county_maximum_per_resolution':
        raise ValueError('Nieobsługiwana normalizacja.')
    missing = {row['warstwa'] for row in layers} - set(config['criteria'])
    if missing:
        raise ValueError(f'Warstwy bez jawnie ustalonych wag: {sorted(missing)}')
    for key, criterion in config['criteria'].items():
        weight = criterion['weight']
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not -1 <= weight <= 1:
            raise ValueError(f'Waga poza [-1,1]: {key}')
        if not criterion['name'] or not criterion['reason']:
            raise ValueError(f'Niekompletne uzasadnienie kryterium: {key}')
    if not 0 <= config['sensitivity_fraction'] <= 1:
        raise ValueError('Niepoprawny zakres analizy wrażliwości.')


def verify_grid(connection, res, layers, model, weights):
    """Niezależne przeliczenie SQL z surowych miar i maksimów źródłowych."""
    table = f'h3_r{res}'
    checks, normalized = [], []
    for j, (layer, weight) in enumerate(zip(layers, weights)):
        col = ident(layer['kolumna'])
        # Liczby punktów są bigint; rzutowanie zapobiega dzieleniu całkowitemu.
        norm = sql.SQL('COALESCE(s.{}::double precision/NULLIF(m.{},0),0)').format(col, ident(f'm{j}'))
        normalized.append(sql.SQL('({} * {})').format(norm, sql.Literal(weight)))
        checks += [sql.SQL('a.{n} IS NULL OR abs(a.{n}-({expected}))>1e-12 OR a.{n}<0 OR a.{n}>1').format(
                       n=ident('n_' + layer['kolumna']), expected=norm),
                   sql.SQL('a.{c} IS DISTINCT FROM s.{c}').format(c=col),
                   sql.SQL('a.{w} IS NULL OR abs(a.{w}-({expected}))>1e-12').format(
                       w=ident('wk_' + layer['kolumna']), expected=normalized[-1])]
    expected_score = sql.SQL('({})/{}').format(sql.SQL('+').join(normalized), sql.Literal(model['denominator']))
    checks.append(sql.SQL('a.ipt IS NULL OR abs(a.ipt-({}))>1e-12 OR a.ipt < -1 OR a.ipt > 1').format(expected_score))
    checks.append(sql.SQL('a.liczba_kryteriow<>{}').format(sql.Literal(model['denominator'])))
    checks.append(sql.SQL('a.ipt_min_wrazliwosc IS NULL OR a.ipt_max_wrazliwosc IS NULL OR a.ipt_min_wrazliwosc>a.ipt+1e-12 OR a.ipt_max_wrazliwosc<a.ipt-1e-12'))
    maxima = sql.SQL(',').join(sql.SQL('MAX({}) {}').format(ident(layer['kolumna']), ident(f'm{j}')) for j, layer in enumerate(layers))
    mismatches = q(connection, sql.SQL('''WITH m AS (SELECT {maxima} FROM {source})
        SELECT COUNT(*) FROM {source} s FULL JOIN {working} a USING(h3_id) CROSS JOIN m
        WHERE s.h3_id IS NULL OR a.h3_id IS NULL OR {checks}''').format(
            maxima=maxima, source=ident('analiza', table), working=ident(WORK, table), checks=sql.SQL(' OR ').join(checks)))[0][0]
    if mismatches:
        raise AssertionError(f'Niezgodność normalizacji lub IPT r{res}: {mismatches}')
    mismatches = q(connection, sql.SQL('''SELECT COUNT(*) FROM {working} a FULL JOIN {result} b USING(h3_id)
        WHERE a.h3_id IS NULL OR b.h3_id IS NULL OR
            (to_jsonb(a)-'geom') IS DISTINCT FROM (to_jsonb(b)-'geom') OR
            ST_SRID(a.geom)<>2180 OR ST_SRID(b.geom)<>3857 OR
            NOT ST_IsValid(a.geom) OR NOT ST_IsValid(b.geom) OR
            NOT ST_Equals(ST_Transform(a.geom,3857),b.geom)''').format(
                working=ident(WORK, table), result=ident(RESULT, table)))[0][0]
    if mismatches:
        raise AssertionError(f'Niezgodność transformacji r{res}: {mismatches}')


def analyze(connection, config, config_hash):
    if not q(connection, "SELECT pg_try_advisory_xact_lock(hashtext('psip_lab2_bdot'))")[0][0]:
        raise RuntimeError('Trwa inna analiza lub import BDOT10K. Spróbuj po zakończeniu.')
    upstream, layers = verify_bdot(connection)
    validate_config(config, layers)
    # W jednej transakcji: w razie błędu poprzedni wynik zostaje zachowany.
    marker = q(connection, "SELECT to_regclass('przejezdnosc.model')")[0][0]
    for schema in [WORK, RESULT]:
        exists = q(connection, 'SELECT to_regnamespace(%s)', (schema,))[0][0]
        if exists and (not marker or q(connection, 'SELECT dokument->>\'marker\' FROM przejezdnosc.model')[0][0] != MARKER):
            raise RuntimeError(f'Schemat {schema} nie jest rozpoznanym wynikiem tego programu.')
    for schema in [WORK, RESULT]:
        q(connection, sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(ident(schema)))
        q(connection, sql.SQL('CREATE SCHEMA {}').format(ident(schema)))
    q(connection, '''CREATE TABLE przejezdnosc.kryteria (
        resolution smallint, warstwa text, nazwa text, kolumna text, jednostka text,
        waga double precision CHECK(waga BETWEEN -1 AND 1), maksimum double precision,
        uwzglednione boolean, uzasadnienie text, PRIMARY KEY(resolution,warstwa))''')
    q(connection, '''CREATE TABLE przejezdnosc.wrazliwosc (
        resolution smallint, warstwa text, mnoznik double precision, waga_bazowa double precision,
        waga_wariantu double precision, ograniczona boolean, rzeczywista_zmiana boolean,
        srednia_zmiana_bezwzgledna double precision, maks_zmiana_bezwzgledna double precision,
        zmiany_znaku integer, korelacja_rang_spearmana double precision)''')
    report = {'marker': MARKER, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'config_sha256': config_hash, 'config': config, 'bdot_source': upstream['zrodlo'],
              'epsg_calculations': 2180, 'epsg_output': 3857, 'grids': []}
    weights = [config['criteria'][layer['warstwa']]['weight'] for layer in layers]
    columns = [layer['kolumna'] for layer in layers]
    for res in [7, 8, 9]:
        name = f'h3_r{res}'
        q(connection, sql.SQL('LOCK TABLE {} IN SHARE MODE').format(ident('analiza', name)))
        records = q(connection, sql.SQL('SELECT h3_id,{} FROM {} ORDER BY h3_id').format(
            sql.SQL(',').join(map(ident, columns)), ident('analiza', name)))
        model = calculate([list(row[1:]) for row in records], weights)
        variation = sensitivity(model, weights, config['sensitivity_fraction'])
        q(connection, sql.SQL('CREATE TABLE {} AS SELECT h3_id,resolution,cell_area_m2,{},geom FROM {}').format(
            ident(WORK, name), sql.SQL(',').join(map(ident, columns)), ident('analiza', name)))
        extra = [('n_'+col, 'double precision') for col in columns] + [('wk_'+col, 'double precision') for col in columns]
        extra += [('ipt','double precision'), ('liczba_kryteriow','integer'),
                  ('liczba_wag_niezerowych','integer'), ('ipt_min_wrazliwosc','double precision'),
                  ('ipt_max_wrazliwosc','double precision'), ('znak_stabilny','boolean')]
        for col, kind in extra:
            q(connection, sql.SQL('ALTER TABLE {} ADD COLUMN {} {}').format(ident(WORK, name), ident(col), sql.SQL(kind)))
        nonzero = sum(weights[j] != 0 for j in model['included'])
        updates = [(row[0], *model['normalized'][i], *model['contributions'][i], model['scores'][i],
                    model['denominator'], nonzero, variation['low'][i], variation['high'][i], variation['stable'][i])
                   for i, row in enumerate(records)]
        with connection.cursor() as cursor:
            command = sql.SQL('UPDATE {table} t SET {assign} FROM (VALUES %s) AS v({cols}) WHERE t.h3_id=v.h3_id').format(
                table=ident(WORK, name), assign=sql.SQL(',').join(sql.SQL('{0}=v.{0}').format(ident(c)) for c, _ in extra),
                cols=sql.SQL(',').join(map(ident, ['h3_id'] + [c for c, _ in extra])))
            execute_values(cursor, command.as_string(connection), updates, page_size=200)
        q(connection, sql.SQL('ALTER TABLE {} ADD PRIMARY KEY(h3_id)').format(ident(WORK, name)))
        q(connection, sql.SQL('CREATE INDEX ON {} USING gist(geom)').format(ident(WORK, name)))
        selected = ['h3_id', 'resolution', 'cell_area_m2'] + columns + [c for c, _ in extra]
        q(connection, sql.SQL('CREATE TABLE {} AS SELECT {}, ST_Transform(geom,3857)::geometry(MultiPolygon,3857) geom FROM {}').format(
            ident(RESULT, name), sql.SQL(',').join(map(ident, selected)), ident(WORK, name)))
        q(connection, sql.SQL('ALTER TABLE {} ADD PRIMARY KEY(h3_id)').format(ident(RESULT, name)))
        q(connection, sql.SQL('CREATE INDEX ON {} USING gist(geom)').format(ident(RESULT, name)))
        for j, layer in enumerate(layers):
            criterion = config['criteria'][layer['warstwa']]
            q(connection, 'INSERT INTO przejezdnosc.kryteria VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
              (res, layer['warstwa'], criterion['name'], layer['kolumna'], layer['jednostka'], weights[j],
               model['maxima'][j], j in model['included'], criterion['reason']))
        for scenario in variation['scenarios']:
            j = scenario['index']
            q(connection, 'INSERT INTO przejezdnosc.wrazliwosc VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
              (res, layers[j]['warstwa'], scenario['factor'], weights[j], scenario['weight'],
               scenario['clipped'], scenario['changed'], scenario['mean_abs_delta'], scenario['max_abs_delta'],
               scenario['sign_changes'], scenario['spearman']))
        verify_grid(connection, res, layers, model, weights)
        grid_report = {'resolution': res, 'cells': len(records), 'criteria': model['denominator'],
                       'nonzero_weights': nonzero, 'ipt_min': min(model['scores']), 'ipt_max': max(model['scores']),
                       'negative': sum(v < 0 for v in model['scores']), 'zero': sum(v == 0 for v in model['scores']),
                       'positive': sum(v > 0 for v in model['scores']),
                       'sensitivity_scenarios': len(variation['scenarios']),
                       'stable_sign_joint_20pct': sum(variation['stable']),
                       'potential_sign_change_joint_20pct': sum(not v for v in variation['stable']),
                       'max_sign_changes_one_weight': max((s['sign_changes'] for s in variation['scenarios']), default=0),
                       'min_spearman_one_weight': min((s['spearman'] for s in variation['scenarios'] if s['spearman'] is not None), default=None),
                       'verified_normalization_scores_and_transform': True}
        report['grids'].append(grid_report)
        print(json.dumps(grid_report, ensure_ascii=False), flush=True)
    q(connection, 'CREATE TABLE przejezdnosc.powiat AS SELECT geom FROM wyniki.powiat')
    q(connection, 'CREATE TABLE przejezdnosc.model (dokument jsonb NOT NULL)')
    q(connection, 'INSERT INTO przejezdnosc.model VALUES (%s)', (Json(report, dumps=lambda value: json.dumps(value, default=str)),))
    return report


def export_results(connection, folder, report, config_bytes):
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'weryfikacja_przejezdnosc.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    (folder/'wagi_modelu.json').write_bytes(config_bytes)
    for table, file in [('kryteria', 'kryteria_i_maksima.csv'), ('wrazliwosc','analiza_wrazliwosci.csv')]:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql.SQL('SELECT * FROM {} ORDER BY resolution,warstwa').format(ident(RESULT, table)))
            rows = [dict(row) for row in cursor.fetchall()]
        with (folder/file).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    for index, table in enumerate(['h3_r7','h3_r8','h3_r9','powiat']):
        command = ['ogr2ogr','-f','GPKG',str(folder/'przejezdnosc_3857.gpkg'),'PG:',
                   '-sql',f'SELECT * FROM przejezdnosc.{table}', '-nln',table,'-overwrite']
        if index:
            command.append('-update')
        subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--export-dir', type=Path)
    args = parser.parse_args()
    if os.getenv('ENVIRONMENT') != 'env-test':
        raise RuntimeError('Analiza jest przeznaczona dla ENVIRONMENT=env-test.')
    content = args.config.read_bytes()
    config = json.loads(content)
    with psycopg2.connect(connect_timeout=15) as connection:
        report = analyze(connection, config, hashlib.sha256(content).hexdigest())
    # Eksport z blokadą importu: raport i geometrie pochodzą z tej samej publikacji.
    if args.export_dir:
        with psycopg2.connect(connect_timeout=15) as connection:
            if not q(connection, "SELECT pg_try_advisory_xact_lock(hashtext('psip_lab2_bdot'))")[0][0]:
                raise RuntimeError('Trwa inne przetwarzanie; odłóż eksport do jego zakończenia.')
            saved = q(connection, 'SELECT dokument FROM przejezdnosc.model')[0][0]
            if saved['created_utc'] != report['created_utc']:
                raise RuntimeError('Wynik został zastąpiony przed eksportem. Uruchom ponownie.')
            export_results(connection, args.export_dir, report, content)
    print('Gotowe: obliczenia EPSG:2180, geometria wynikowa EPSG:3857.', flush=True)


if __name__ == '__main__':
    main()

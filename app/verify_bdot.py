"""Kontrola liczby kolumn, bilansów, geometrii i transformacji wyników BDOT10K."""
import argparse
import csv
import json
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


def verify(connection):
    report = {'epsg_obliczen': 2180, 'epsg_wynikow': 3857, 'siatki': []}
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('SELECT * FROM wyniki.zrodlo')
        report['zrodlo'] = dict(cursor.fetchone())
        cursor.execute('SELECT * FROM wyniki.warstwy ORDER BY warstwa')
        layers = [dict(row) for row in cursor.fetchall()]
        report['liczba_warstw'] = len(layers)
        report['liczba_obiektow_bdot'] = sum(row['liczba_w_powiecie'] for row in layers)
        if not layers:
            raise AssertionError('Brak zaimportowanych warstw BDOT10K.')
        cursor.execute('SELECT count(*) n,bool_and(poprawne) valid FROM wyniki.kontrola')
        control = cursor.fetchone()
        assert control['n'] == len(layers) * 3 and control['valid'], dict(control)
        report['liczba_poprawnych_bilansow'] = control['n']
        for layer in layers:
            cursor.execute(sql.SQL('SELECT count(*) FILTER(WHERE geom IS NULL OR ST_IsEmpty(geom) OR NOT ST_IsValid(geom) OR ST_SRID(geom)<>2180) invalid FROM {}').format(sql.Identifier('bdot', layer['warstwa'])))
            assert cursor.fetchone()['invalid'] == 0, layer['warstwa']
        for res in [7,8,9]:
            name = f'h3_r{res}'
            cursor.execute(sql.SQL('''SELECT count(*) n,
                count(*) FILTER(WHERE geom IS NULL OR ST_IsEmpty(geom) OR NOT ST_IsValid(geom) OR ST_SRID(geom)<>3857) invalid
                FROM {}''').format(sql.Identifier('wyniki', name)))
            count = cursor.fetchone()
            assert count['n'] > 0 and count['invalid'] == 0, dict(count)
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='wyniki' AND table_name=%s", (name,))
            columns = {row['column_name'] for row in cursor.fetchall()}
            assert all(layer['kolumna'] in columns for layer in layers)
            assert columns == {layer['kolumna'] for layer in layers} | {'h3_id','resolution','cell_area_m2','geom'}
            cursor.execute(sql.SQL('''SELECT count(*) invalid FROM {} WHERE
                ST_SRID(geom)<>2180 OR ST_SRID(full_geom)<>2180 OR
                NOT ST_IsValid(geom) OR NOT ST_IsValid(full_geom)''').format(sql.Identifier('analiza',name)))
            assert cursor.fetchone()['invalid'] == 0, f'Geometria robocza: {name}'
            cursor.execute(sql.SQL('''SELECT count(*) mismatch FROM {working} a FULL JOIN {result} w USING(h3_id)
                WHERE a.h3_id IS NULL OR w.h3_id IS NULL OR
                    (to_jsonb(a)-'geom'-'full_geom') IS DISTINCT FROM (to_jsonb(w)-'geom') OR
                    NOT ST_Equals(ST_Transform(a.geom,3857),w.geom) OR ST_SRID(a.geom)<>2180''').format(
                        working=sql.Identifier('analiza',name), result=sql.Identifier('wyniki',name)))
            assert cursor.fetchone()['mismatch'] == 0, f'Transformacja lub wartości: {name}'
            report['siatki'].append({'resolution':res,'komorki':count['n'],'kolumny_bdot':len(layers),'epsg':3857})
    return report, layers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    parser.add_argument('--csv')
    args = parser.parse_args()
    with psycopg2.connect(connect_timeout=15) as connection:
        report, layers = verify(connection)
    output = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(output)
    if args.json:
        Path(args.json).write_text(output,encoding='utf-8')
    if args.csv:
        with open(args.csv,'w',encoding='utf-8-sig',newline='') as stream:
            writer = csv.DictWriter(stream,fieldnames=list(layers[0]))
            writer.writeheader()
            writer.writerows(layers)


if __name__ == '__main__':
    main()

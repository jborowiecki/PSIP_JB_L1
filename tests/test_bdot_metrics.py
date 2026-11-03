"""Test miar w EPSG:2180, w transakcji wycofywanej po wykonaniu."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import psycopg2
from python.bdot_pipeline import aggregate_layer


class MetricTests(unittest.TestCase):
    def test_crossings_boundaries_and_repeated_line_segments(self):
        connection = psycopg2.connect(connect_timeout=15)
        try:
            with connection.cursor() as cursor:
                for schema in ['_lab2_analiza', '_lab2_bdot', '_lab2_wyniki']:
                    cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
                cursor.execute('''CREATE TABLE IF NOT EXISTS _lab2_wyniki.kontrola
                    (resolution integer,warstwa text,suma_zrodlowa double precision,
                     suma_w_siatce double precision,roznica double precision,
                     tolerancja double precision,poprawne boolean,PRIMARY KEY(resolution,warstwa))''')
                cursor.execute('''CREATE TABLE _lab2_analiza.h3_r0
                    (h3_id text PRIMARY KEY,geom geometry(MultiPolygon,2180))''')
                cursor.execute('''INSERT INTO _lab2_analiza.h3_r0 VALUES
                    ('a',ST_Multi(ST_MakeEnvelope(500000,500000,500001,500001,2180))),
                    ('b',ST_Multi(ST_MakeEnvelope(500001,500000,500002,500001,2180)))''')
                cursor.execute('ALTER TABLE _lab2_analiza.h3_r0 ADD COLUMN full_geom geometry(MultiPolygon,2180)')
                cursor.execute('UPDATE _lab2_analiza.h3_r0 SET full_geom=geom')
                for name, kind in [('lab2_points','MultiPoint'),('lab2_lines','MultiLineString'),('lab2_polygons','MultiPolygon')]:
                    cursor.execute(f'CREATE TABLE _lab2_bdot.{name}(feature_id serial PRIMARY KEY,geom geometry({kind},2180))')
                cursor.execute('''INSERT INTO _lab2_bdot.lab2_points(geom) VALUES
                    (ST_GeomFromText('MULTIPOINT((500000.5 500000.5),(500001 500000.5),(500002 500000.5))',2180))''')
                cursor.execute('''INSERT INTO _lab2_bdot.lab2_lines(geom) VALUES
                    (ST_GeomFromText('MULTILINESTRING((500000 500000.5,500002 500000.5,500000 500000.5),(500001 500000,500001 500001))',2180))''')
                cursor.execute('''INSERT INTO _lab2_bdot.lab2_polygons(geom) VALUES
                    (ST_Multi(ST_MakeEnvelope(500000,500000,500002,500001,2180)))''')
            aggregate_layer(connection,0,'lab2_points',0,'test_points',3)
            aggregate_layer(connection,0,'lab2_lines',1,'test_lines',5)
            aggregate_layer(connection,0,'lab2_polygons',2,'test_area',2)
            with connection.cursor() as cursor:
                cursor.execute('SELECT test_points,test_lines,test_area FROM _lab2_analiza.h3_r0 ORDER BY h3_id')
                a,b = cursor.fetchall()
                self.assertEqual(a[0],2)
                self.assertEqual(b[0],1)
                self.assertAlmostEqual(a[1],3)
                self.assertAlmostEqual(b[1],2)
                self.assertAlmostEqual(a[2],1)
                self.assertAlmostEqual(b[2],1)
        finally:
            connection.rollback()
            connection.close()


if __name__ == '__main__':
    unittest.main()

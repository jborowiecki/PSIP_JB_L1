"""Połączenie QGIS z istniejącym PostGIS env-test, bez hasła w projekcie."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def prepare_connection(folder):
    container = 'remote-db-env-test-postgis-1'
    details = json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0]
    config = dict(value.split('=',1) for value in details['Config']['Env'] if '=' in value)
    if not details['State']['Running']:
        raise RuntimeError('Kontener env-test nie działa.')
    port = details['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
    target = Path(os.environ.get('PGPASSFILE') or Path(os.environ['APPDATA'])/'postgresql/pgpass.conf')
    target.parent.mkdir(parents=True,exist_ok=True)
    def escape(value):
        return value.replace('\\','\\\\').replace(':','\\:')
    prefix = ':'.join(map(escape,['localhost',port,config['POSTGRES_DB'],config['POSTGRES_USER']]))+':'
    existing = target.read_text(encoding='utf-8-sig').splitlines() if target.exists() else []
    lines = [prefix+escape(config['POSTGRES_PASSWORD'])]+[line for line in existing if not line.startswith(prefix)]
    temporary = target.with_name(target.name+'.psip.tmp')
    temporary.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    temporary.replace(target)
    query = """SELECT n.nspname AS schema,c.relname AS name,
        COALESCE((SELECT string_agg(a.attname,',' ORDER BY a.attnum)
            FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
            WHERE i.indrelid=c.oid AND i.indisprimary),'') AS key,
        COALESCE((SELECT json_agg(json_build_object('column',g.f_geometry_column,'srid',g.srid,'type',g.type))
            FROM geometry_columns g WHERE g.f_table_schema=n.nspname AND g.f_table_name=c.relname),'[]'::json) AS geometry
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','v','m') AND (n.nspname IN ('bdot','analiza','wyniki','grid','przejezdnosc_robocza','przejezdnosc')
            OR (n.nspname='public' AND c.relname='powiat')) ORDER BY n.nspname,c.relname"""
    result = subprocess.run(['docker','exec',container,'psql','-X','-U',config['POSTGRES_USER'],'-d',config['POSTGRES_DB'],
                             '-t','-A','-c',f'SELECT json_agg(t) FROM ({query}) t'],capture_output=True,text=True,check=True)
    manifest = {'host':'localhost','port':port,'database':config['POSTGRES_DB'],'user':config['POSTGRES_USER'],
                'connection_name':'PSIP_env_test_Docker','tables':json.loads(result.stdout)}
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'polaczenie_postgis.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Połączenie lokalne gotowe. Tabel aplikacyjnych: {len(manifest["tables"])}. Hasło poza projektem QGIS.')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    folder = (args.output_dir or root/'qgis/przejezdnosc').resolve()
    prepare_connection(folder)
    qgis_python = Path(r'C:\Program Files\QGIS 3.36.0\bin\python-qgis.bat')
    subprocess.run([str(qgis_python),str(root/'qgis/connect_postgis_project.py'),'--folder',str(folder)],check=True)


if __name__ == '__main__':
    main()

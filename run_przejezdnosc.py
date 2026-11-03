"""Uruchom w PyCharmie: testy, analiza, eksport i projekt QGIS."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from configure_qgis_postgis import prepare_connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--no-qgis', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = (args.output_dir or root/'qgis/przejezdnosc').resolve()
    output.mkdir(parents=True, exist_ok=True)
    container = 'remote-db-env-test-postgis-1'
    details = json.loads(subprocess.check_output(['docker','inspect',container], text=True))[0]
    config = dict(value.split('=',1) for value in details['Config']['Env'] if '=' in value)
    env = os.environ.copy()
    env.update(PGHOST=container, PGPORT='5432', PGUSER=config['POSTGRES_USER'],
               PGDATABASE=config['POSTGRES_DB'], PGPASSWORD=config['POSTGRES_PASSWORD'], ENVIRONMENT='env-test')
    image = 'psip-jb-przejezdnosc-python'
    if not args.no_build:
        subprocess.run(['docker','build','-f','docker/python/Dockerfile','-t',image,'.'], cwd=root, check=True)
    command = ['docker','run','--rm','--network','remote-db-env-test_default',
               '--mount',f'type=bind,source={output},target=/exports']
    for key in ['PGHOST','PGPORT','PGUSER','PGDATABASE','PGPASSWORD','ENVIRONMENT']:
        command.extend(['--env',key])
    subprocess.run(command + [image,'python','/tests/test_passability_model.py'], env=env, check=True)
    subprocess.run(command + [image,'python','/app/przejezdnosc.py','--export-dir','/exports'], env=env, check=True)
    if not args.no_qgis:
        qgis_python = Path(r'C:\Program Files\QGIS 3.36.0\bin\python-qgis.bat')
        if qgis_python.exists():
            subprocess.run([str(qgis_python),str(root/'qgis/create_przejezdnosc_project.py'),
                            '--folder',str(output)], cwd=root, check=True)
            prepare_connection(output)
            subprocess.run([str(qgis_python),str(root/'qgis/connect_postgis_project.py'),
                            '--folder',str(output)], cwd=root, check=True)
        else:
            print('Eksport gotowy. Do utworzenia projektu QGIS uruchom qgis/create_przejezdnosc_project.py w Pythonie QGIS.')
    print(f'Wyniki analizy: {output}')


if __name__ == '__main__':
    main()

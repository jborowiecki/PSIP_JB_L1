"""Uruchomienie importu i analizy BDOT10K na istniejącej bazie env-test."""
import json
import os
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parent
    container = 'remote-db-env-test-postgis-1'
    details = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
    config = dict(value.split('=', 1) for value in details['Config']['Env'] if '=' in value)
    env = os.environ.copy()
    env.update(PGHOST=container, PGPORT='5432', PGUSER=config['POSTGRES_USER'],
               PGDATABASE=config['POSTGRES_DB'], PGPASSWORD=config['POSTGRES_PASSWORD'], ENVIRONMENT='env-test')
    cache = root / 'data' / 'bdot'
    cache.mkdir(parents=True, exist_ok=True)
    image = 'psip-jb-bdot-python'
    subprocess.run(['docker', 'build', '-f', 'docker/python/Dockerfile', '-t', image, '.'], cwd=root, check=True)
    command = ['docker', 'run', '--rm', '--network', 'remote-db-env-test_default',
               '--mount', f'type=bind,source={cache},target=/bdot']
    for key in ['PGHOST', 'PGPORT', 'PGUSER', 'PGDATABASE', 'PGPASSWORD', 'ENVIRONMENT']:
        command.extend(['--env', key])
    subprocess.run(command + [image, 'python', '/app/bdot.py'], cwd=root, env=env, check=True)
    subprocess.run(command + [image, 'python', '/app/verify_bdot.py'], cwd=root, env=env, check=True)


if __name__ == '__main__':
    main()
